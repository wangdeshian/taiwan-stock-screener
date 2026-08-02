"""QuotaTray（tools/quota_tray）的解析邏輯測試。

只測純資料層，不碰 pystray / tkinter，所以在 CI 的無頭環境也能跑。
"""

from __future__ import annotations

import json
import os
from datetime import datetime, timedelta, timezone

import pytest

from tools.quota_tray.config import (
    AppConfig,
    ClaudeConfig,
    CodexConfig,
    GeminiConfig,
    home_roots,
    load_config,
    resolve_dirs,
    write_config,
)
from tools.quota_tray.models import (
    ProviderSnapshot,
    QuotaWindow,
    format_countdown,
    format_tokens,
    format_window_label,
    parse_iso,
)
from tools.quota_tray.providers import ClaudeProvider, CodexProvider, GeminiProvider
from tools.quota_tray.service import QuotaService

NOW = datetime(2026, 8, 1, 12, 0, 0, tzinfo=timezone.utc)


def write(path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")


def stamp(when: datetime) -> str:
    return when.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")


# --------------------------------------------------------------------------
# Codex
# --------------------------------------------------------------------------


def codex_provider(root, **overrides):
    return CodexProvider(CodexConfig(**overrides), [root])


def test_codex_reads_rate_limits_nested_in_payload(tmp_path):
    write(
        tmp_path / ".codex/sessions/2026/08/01/rollout-1.jsonl",
        "\n".join(
            [
                json.dumps({"type": "session_meta", "payload": {"id": "abc"}}),
                json.dumps(
                    {
                        "type": "event_msg",
                        "payload": {
                            "type": "token_count",
                            "rate_limits": {
                                "primary": {
                                    "used_percent": 9.0,
                                    "window_minutes": 300,
                                    "resets_in_seconds": 3600,
                                },
                                "secondary": {
                                    "used_percent": 42.5,
                                    "window_minutes": 10080,
                                    "resets_in_seconds": 86400,
                                },
                            },
                        },
                    }
                ),
            ]
        ),
    )

    snapshot = codex_provider(tmp_path).snapshot(NOW)

    assert snapshot.available
    assert [w.label for w in snapshot.windows] == ["5 小時", "每週"]
    assert snapshot.windows[0].used_percent == pytest.approx(9.0)
    assert snapshot.windows[0].remaining_percent == pytest.approx(91.0)
    assert snapshot.remaining_percent == pytest.approx(91.0)
    assert snapshot.windows[0].resets_at == NOW + timedelta(seconds=3600)


def test_codex_accepts_top_level_and_camel_case(tmp_path):
    write(
        tmp_path / ".codex/sessions/rollout-2.jsonl",
        json.dumps({"rateLimits": {"primary": {"usedPercent": 75, "windowMinutes": 60}}}),
    )

    snapshot = codex_provider(tmp_path).snapshot(NOW)

    assert snapshot.available
    # 60 分鐘會正規化成「1 小時」，各家單位不同也能顯示一致
    assert snapshot.windows[0].label == "1 小時"
    assert snapshot.windows[0].remaining_percent == pytest.approx(25.0)


def test_codex_uses_newest_event_in_newest_file(tmp_path):
    sessions = tmp_path / ".codex/sessions"
    older = sessions / "rollout-old.jsonl"
    newer = sessions / "rollout-new.jsonl"
    write(older, json.dumps({"rate_limits": {"primary": {"used_percent": 10}}}))
    write(
        newer,
        "\n".join(
            json.dumps({"rate_limits": {"primary": {"used_percent": value}}})
            for value in (20, 31)
        ),
    )

    # 明確設定 mtime，不依賴寫入順序。
    os.utime(older, (1_700_000_000, 1_700_000_000))
    os.utime(newer, (1_800_000_000, 1_800_000_000))

    snapshot = codex_provider(tmp_path).snapshot(NOW)
    assert snapshot.windows[0].used_percent == pytest.approx(31.0)


def test_codex_unknown_window_keys_still_reported(tmp_path):
    write(
        tmp_path / ".codex/sessions/rollout.jsonl",
        json.dumps({"rate_limits": {"weird_new_name": {"used_percent": 5}}}),
    )

    snapshot = codex_provider(tmp_path).snapshot(NOW)

    assert snapshot.available
    assert snapshot.windows[0].label == "weird_new_name"
    assert snapshot.windows[0].remaining_percent == pytest.approx(95.0)


def test_codex_missing_directory_is_unavailable_not_zero(tmp_path):
    snapshot = codex_provider(tmp_path).snapshot(NOW)

    assert not snapshot.available
    assert snapshot.windows == []
    assert snapshot.remaining_percent is None


def test_codex_session_without_rate_limits_is_unavailable(tmp_path):
    write(
        tmp_path / ".codex/sessions/rollout.jsonl",
        json.dumps({"type": "event_msg", "payload": {"type": "agent_message"}}),
    )

    assert not codex_provider(tmp_path).snapshot(NOW).available


def test_codex_survives_malformed_lines(tmp_path):
    write(
        tmp_path / ".codex/sessions/rollout.jsonl",
        "\n".join(
            [
                "{ this is not json but mentions rate_limits",
                json.dumps({"rate_limits": {"primary": {"used_percent": 12}}}),
            ]
        ),
    )

    snapshot = codex_provider(tmp_path).snapshot(NOW)
    assert snapshot.windows[0].used_percent == pytest.approx(12.0)


# --------------------------------------------------------------------------
# Claude
# --------------------------------------------------------------------------


def assistant_line(when: datetime, msg_id: str, request_id: str, **tokens) -> str:
    usage = {
        "input_tokens": tokens.get("input", 100),
        "output_tokens": tokens.get("output", 50),
        "cache_creation_input_tokens": tokens.get("cache_creation", 0),
        "cache_read_input_tokens": tokens.get("cache_read", 0),
    }
    return json.dumps(
        {
            "type": "assistant",
            "timestamp": stamp(when),
            "requestId": request_id,
            "message": {"id": msg_id, "usage": usage},
        }
    )


def claude_provider(root, **overrides):
    return ClaudeProvider(ClaudeConfig(**overrides), [root])


def test_claude_sums_tokens_inside_rolling_window(tmp_path):
    write(
        tmp_path / ".claude/projects/proj-a/session1.jsonl",
        "\n".join(
            [
                assistant_line(NOW - timedelta(minutes=10), "m1", "r1", input=1000, output=200),
                assistant_line(NOW - timedelta(hours=1), "m2", "r2", input=500, output=100),
                # 6 小時前：落在 5 小時窗之外，但仍在 7 天窗內
                assistant_line(NOW - timedelta(hours=6), "m3", "r3", input=9000, output=1),
            ]
        ),
    )

    snapshot = claude_provider(tmp_path).snapshot(NOW)

    assert snapshot.available
    assert snapshot.windows[0].label == "5 小時"
    assert snapshot.windows[0].detail == "1.8K tokens · 2 則回應"
    assert snapshot.windows[1].label == "最近 7 天"
    assert snapshot.windows[1].detail == "10.8K tokens · 3 則回應"


def test_claude_shows_no_percent_without_budget(tmp_path):
    write(
        tmp_path / ".claude/projects/proj/session.jsonl",
        assistant_line(NOW, "m1", "r1"),
    )

    snapshot = claude_provider(tmp_path).snapshot(NOW)

    assert snapshot.available
    assert snapshot.windows[0].used_percent is None
    assert snapshot.remaining_percent is None
    assert "非官方額度" in snapshot.note


def test_claude_percent_uses_configured_budget(tmp_path):
    write(
        tmp_path / ".claude/projects/proj/session.jsonl",
        assistant_line(NOW, "m1", "r1", input=2000, output=500),
    )

    snapshot = claude_provider(tmp_path, session_token_budget=10_000).snapshot(NOW)

    assert snapshot.windows[0].used_percent == pytest.approx(25.0)
    assert snapshot.remaining_percent == pytest.approx(75.0)


def test_claude_deduplicates_same_response_across_files(tmp_path):
    line = assistant_line(NOW, "m1", "r1", input=1000, output=0)
    write(tmp_path / ".claude/projects/proj-a/session.jsonl", line)
    write(tmp_path / ".claude/projects/proj-b/session.jsonl", line)

    snapshot = claude_provider(tmp_path).snapshot(NOW)
    assert snapshot.windows[0].detail == "1.0K tokens · 1 則回應"


def test_claude_counts_all_four_token_kinds(tmp_path):
    write(
        tmp_path / ".claude/projects/proj/session.jsonl",
        assistant_line(NOW, "m1", "r1", input=1, output=2, cache_creation=3, cache_read=4),
    )

    snapshot = claude_provider(tmp_path, session_token_budget=10).snapshot(NOW)
    assert snapshot.windows[0].used_percent == pytest.approx(100.0)


def test_claude_ignores_user_lines_and_broken_json(tmp_path):
    write(
        tmp_path / ".claude/projects/proj/session.jsonl",
        "\n".join(
            [
                json.dumps({"type": "user", "message": {"role": "user", "content": "hi"}}),
                '{ "usage" broken',
                assistant_line(NOW, "m1", "r1", input=10, output=0),
            ]
        ),
    )

    snapshot = claude_provider(tmp_path).snapshot(NOW)
    assert snapshot.windows[0].detail == "10 tokens · 1 則回應"


def test_claude_missing_directory_is_unavailable(tmp_path):
    snapshot = claude_provider(tmp_path).snapshot(NOW)
    assert not snapshot.available
    assert snapshot.remaining_percent is None


def test_claude_cache_returns_same_result(tmp_path):
    write(
        tmp_path / ".claude/projects/proj/session.jsonl",
        assistant_line(NOW, "m1", "r1", input=1000, output=0),
    )

    provider = claude_provider(tmp_path)
    first = provider.snapshot(NOW)
    second = provider.snapshot(NOW)

    assert first.windows[0].detail == second.windows[0].detail


def test_claude_session_reset_is_when_oldest_entry_leaves_window(tmp_path):
    oldest = NOW - timedelta(hours=2)
    write(
        tmp_path / ".claude/projects/proj/session.jsonl",
        "\n".join(
            [
                assistant_line(oldest, "m1", "r1", input=10, output=0),
                assistant_line(NOW, "m2", "r2", input=10, output=0),
            ]
        ),
    )

    snapshot = claude_provider(tmp_path).snapshot(NOW)
    assert snapshot.windows[0].resets_at == oldest + timedelta(hours=5)


# --------------------------------------------------------------------------
# Gemini
# --------------------------------------------------------------------------


def gemini_logs(entries: list[tuple[datetime, str]]) -> str:
    return json.dumps(
        [
            {"sessionId": "s", "messageId": 0, "timestamp": stamp(when), "type": kind}
            for when, kind in entries
        ]
    )


def gemini_provider(root, **overrides):
    defaults = {
        "daily_request_limit": 100,
        "minute_request_limit": 10,
        "daily_reset_timezone": "UTC",
    }
    defaults.update(overrides)
    return GeminiProvider(GeminiConfig(**defaults), [root])


def test_gemini_counts_today_and_last_minute(tmp_path):
    write(
        tmp_path / ".gemini/tmp/hash1/logs.json",
        gemini_logs(
            [
                (NOW - timedelta(seconds=10), "user"),
                (NOW - timedelta(seconds=30), "user"),
                (NOW - timedelta(hours=4), "user"),
                (NOW - timedelta(seconds=20), "gemini"),
            ]
        ),
    )

    snapshot = gemini_provider(tmp_path).snapshot(NOW)

    assert snapshot.available
    assert snapshot.windows[0].detail == "3 / 100 次"
    assert snapshot.windows[0].used_percent == pytest.approx(3.0)
    assert snapshot.windows[1].detail == "2 / 10 次"


def test_gemini_aggregates_across_project_dirs(tmp_path):
    write(
        tmp_path / ".gemini/tmp/hash1/logs.json",
        gemini_logs([(NOW - timedelta(seconds=100), "user")]),
    )
    write(
        tmp_path / ".gemini/tmp/hash2/logs.json",
        gemini_logs([(NOW - timedelta(seconds=200), "user")]),
    )

    snapshot = gemini_provider(tmp_path).snapshot(NOW)
    assert snapshot.windows[0].detail == "2 / 100 次"


def test_gemini_yesterday_does_not_count_toward_today(tmp_path):
    early = datetime(2026, 8, 1, 1, 0, 0, tzinfo=timezone.utc)
    write(
        tmp_path / ".gemini/tmp/hash1/logs.json",
        gemini_logs([(early - timedelta(hours=2), "user"), (early - timedelta(seconds=60), "user")]),
    )

    snapshot = gemini_provider(tmp_path).snapshot(early)
    assert snapshot.windows[0].detail == "1 / 100 次"


def test_gemini_reset_timezone_shifts_day_boundary(tmp_path):
    # UTC 01:00 = 太平洋時間前一天 18:00，所以這兩筆在太平洋時區算同一天。
    early = datetime(2026, 8, 1, 1, 0, 0, tzinfo=timezone.utc)
    write(
        tmp_path / ".gemini/tmp/hash1/logs.json",
        gemini_logs([(early - timedelta(hours=2), "user"), (early - timedelta(seconds=60), "user")]),
    )

    provider = gemini_provider(tmp_path, daily_reset_timezone="America/Los_Angeles")
    snapshot = provider.snapshot(early)
    assert snapshot.windows[0].detail == "2 / 100 次"


def test_gemini_unknown_timezone_falls_back_with_note(tmp_path):
    write(
        tmp_path / ".gemini/tmp/hash1/logs.json",
        gemini_logs([(NOW - timedelta(seconds=10), "user")]),
    )

    snapshot = gemini_provider(tmp_path, daily_reset_timezone="Not/AZone").snapshot(NOW)

    assert snapshot.available
    assert "不可用" in snapshot.note


def test_gemini_missing_directory_is_unavailable(tmp_path):
    snapshot = gemini_provider(tmp_path).snapshot(NOW)
    assert not snapshot.available
    assert snapshot.remaining_percent is None


def test_gemini_empty_logs_are_unavailable_not_zero_percent(tmp_path):
    write(tmp_path / ".gemini/tmp/hash1/logs.json", "[]")

    assert not gemini_provider(tmp_path).snapshot(NOW).available


# --------------------------------------------------------------------------
# 設定檔與路徑
# --------------------------------------------------------------------------


def test_missing_config_falls_back_to_defaults(tmp_path):
    config, error = load_config(tmp_path / "nope.json")

    assert error is None
    assert config == AppConfig()


def test_partial_config_keeps_defaults_for_omitted_keys(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"refresh_seconds": 15, "claude": {"enabled": false}}', encoding="utf-8")

    config, error = load_config(path)

    assert error is None
    assert config.refresh_seconds == 15
    assert config.claude.enabled is False
    assert config.codex.enabled is True
    assert config.gemini.daily_request_limit == 1_000


def test_broken_config_falls_back_with_message(tmp_path):
    path = tmp_path / "config.json"
    path.write_text("{ not json", encoding="utf-8")

    config, error = load_config(path)

    assert error is not None
    assert config == AppConfig()


def test_unknown_config_keys_are_ignored(tmp_path):
    path = tmp_path / "config.json"
    path.write_text('{"codex": {"enabled": false, "made_up_key": 1}}', encoding="utf-8")

    config, error = load_config(path)

    assert error is None
    assert config.codex.enabled is False
    assert not hasattr(config.codex, "made_up_key")


def test_config_round_trip(tmp_path):
    path = tmp_path / "config.json"
    config = AppConfig()
    config.refresh_seconds = 20
    config.claude.session_token_budget = 5_000_000
    write_config(config, path)

    reloaded, error = load_config(path)

    assert error is None
    assert reloaded == config


def test_home_roots_deduplicates_and_keeps_order(tmp_path):
    config = AppConfig()
    config.search_wsl = False
    config.extra_home_dirs = [str(tmp_path), str(tmp_path)]

    roots = home_roots(config)

    assert roots[0] == __import__("pathlib").Path.home()
    assert roots.count(tmp_path) == 1


def test_resolve_dirs_only_returns_existing(tmp_path):
    (tmp_path / "a/.codex/sessions").mkdir(parents=True)
    other = tmp_path / "b"
    other.mkdir()

    found = resolve_dirs(None, ".codex/sessions", [tmp_path / "a", other])

    assert found == [tmp_path / "a/.codex/sessions"]


def test_resolve_dirs_honours_explicit_setting(tmp_path):
    explicit = tmp_path / "custom"
    explicit.mkdir()

    assert resolve_dirs(str(explicit), ".codex/sessions", [tmp_path]) == [explicit]
    assert resolve_dirs(str(tmp_path / "gone"), ".codex/sessions", [tmp_path]) == []


# --------------------------------------------------------------------------
# 格式化與服務層
# --------------------------------------------------------------------------


def test_format_tokens():
    assert format_tokens(0) == "0"
    assert format_tokens(999) == "999"
    assert format_tokens(1_000) == "1.0K"
    assert format_tokens(10_801) == "10.8K"
    assert format_tokens(2_500_000) == "2.5M"


def test_format_window_label():
    assert format_window_label(1) == "1 分鐘"
    assert format_window_label(300) == "5 小時"
    assert format_window_label(1_440) == "每日"
    assert format_window_label(10_080) == "每週"
    assert format_window_label(2_880) == "2 天"


def test_format_countdown():
    assert format_countdown(NOW - timedelta(seconds=5), NOW) == "即將重置"
    assert format_countdown(NOW + timedelta(seconds=30), NOW) == "30 秒後重置"
    assert format_countdown(NOW + timedelta(minutes=10), NOW) == "10 分後重置"
    assert format_countdown(NOW + timedelta(hours=2), NOW) == "2 小時後重置"
    assert format_countdown(NOW + timedelta(seconds=9_000), NOW) == "2 小時 30 分後重置"


def test_quota_window_clamps_percent():
    assert QuotaWindow("x", used_percent=140).used_percent == 100
    assert QuotaWindow("x", used_percent=-10).used_percent == 0
    assert QuotaWindow("x").used_percent is None


def test_primary_window_prefers_one_with_percent():
    snapshot = ProviderSnapshot(
        id="x",
        display_name="X",
        short_name="X",
        available=True,
        windows=[QuotaWindow("無百分比", detail="123"), QuotaWindow("有百分比", used_percent=40)],
    )

    assert snapshot.primary_window.label == "有百分比"
    assert snapshot.remaining_percent == pytest.approx(60.0)


def test_parse_iso_handles_z_suffix_and_offsets():
    assert parse_iso("2026-08-01T12:00:00Z") == NOW
    assert parse_iso("2026-08-01T12:00:00.000Z") == NOW
    assert parse_iso("2026-08-01T20:00:00+08:00") == NOW
    assert parse_iso("not a date") is None
    assert parse_iso(None) is None


def test_service_summary_and_safety_level(tmp_path):
    write(
        tmp_path / ".codex/sessions/rollout.jsonl",
        json.dumps({"rate_limits": {"primary": {"used_percent": 9, "window_minutes": 300}}}),
    )
    write(
        tmp_path / ".gemini/tmp/h/logs.json",
        gemini_logs([(NOW - timedelta(seconds=10), "user")]),
    )

    config = AppConfig()
    config.gemini.daily_request_limit = 100
    service = QuotaService(config, roots=[tmp_path])
    snapshots = service.snapshots(NOW)

    assert service.summary_line(snapshots) == "CX 91%  GM 99%"
    assert service.safety_level(snapshots) == pytest.approx(91.0)


def test_icon_colour_thresholds():
    from tools.quota_tray.icon import GREEN, GREY, ORANGE, RED, color_for

    assert color_for(None) == GREY
    assert color_for(100) == GREEN
    assert color_for(50) == GREEN
    assert color_for(49.9) == ORANGE
    assert color_for(20) == ORANGE
    assert color_for(19.9) == RED


def test_icon_image_renders_at_tray_size():
    pytest.importorskip("PIL", reason="系統匣圖示需要 Pillow，純資料層測試不需要")
    from tools.quota_tray.icon import make_icon_image

    image = make_icon_image(62, size=16)
    assert image.size == (16, 16)
    assert image.mode == "RGBA"
    # 有資料時環上要真的有顏色，不能是全透明。
    assert any(pixel[3] > 0 for pixel in image.getdata())

    # 無資料的圖示要跟 0% 明顯不同，避免「沒讀到」被誤看成「用完了」。
    assert list(make_icon_image(None, 16).getdata()) != list(make_icon_image(0, 16).getdata())


def test_service_isolates_a_failing_provider(tmp_path):
    class Boom:
        id = "boom"
        display_name = "Boom"
        short_name = "BM"

        def snapshot(self, now):
            raise RuntimeError("壞掉了")

    service = QuotaService(AppConfig(), roots=[tmp_path])
    service.providers = [Boom()]
    snapshots = service.snapshots(NOW)

    assert not snapshots[0].available
    assert "壞掉了" in snapshots[0].note
