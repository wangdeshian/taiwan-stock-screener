from __future__ import annotations

from taiwan_stock_screener.database.session import SessionLocal, init_db
from taiwan_stock_screener.logging_config import configure_logging
from taiwan_stock_screener.services.update_service import DailyUpdateService


def main() -> None:
    configure_logging()
    init_db()
    db = SessionLocal()
    try:
        result = DailyUpdateService(db).run()
        print(result)
    finally:
        db.close()


if __name__ == "__main__":
    main()
