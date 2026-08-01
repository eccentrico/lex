"""
NSE Market Calendar Service

Provides market hours, holiday checking, and trading day utilities.
Essential for preventing wasteful agent execution on non-trading days.

Holiday data is sourced in priority order:
  1. Dynamically fetched from NSE India API (cached 24 h in /tmp/nse_holidays_cache.json)
  2. Static fallback lists embedded below (updated annually)
"""

import json
import logging
import os
import time
from datetime import datetime, date, timedelta
from typing import Optional, List, Set
import pytz

# Configure logging
logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# IST timezone
IST = pytz.timezone("Asia/Kolkata")


class NSEMarketCalendar:
    """
    NSE Market Calendar with holiday awareness and trading hours.
    
    Usage:
        calendar = NSEMarketCalendar()
        
        if calendar.is_market_open():
            # Execute trading logic
            pass
        
        if calendar.is_trading_day():
            # Run analysis (even outside market hours)
            pass
    """
    
    # NSE Trading Hours (IST)
    MARKET_OPEN_HOUR = 9
    MARKET_OPEN_MINUTE = 15
    MARKET_CLOSE_HOUR = 15
    MARKET_CLOSE_MINUTE = 30
    
    # Pre-market session
    PRE_MARKET_START_HOUR = 9
    PRE_MARKET_START_MINUTE = 0
    
    # NSE Holidays 2024 (update annually)
    # Source: https://www.nseindia.com/resources/exchange-communication-holidays
    HOLIDAYS_2024 = [
        "2024-01-26",  # Republic Day
        "2024-03-08",  # Maha Shivaratri
        "2024-03-25",  # Holi
        "2024-03-29",  # Good Friday
        "2024-04-11",  # Id-Ul-Fitr (Ramadan)
        "2024-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
        "2024-04-17",  # Ram Navami
        "2024-04-21",  # Mahavir Jayanti
        "2024-05-01",  # Maharashtra Day
        "2024-05-23",  # Buddha Purnima
        "2024-06-17",  # Eid ul-Adha (Bakrid)
        "2024-07-17",  # Muharram
        "2024-08-15",  # Independence Day
        "2024-10-02",  # Mahatma Gandhi Jayanti
        "2024-11-01",  # Diwali Laxmi Pujan
        "2024-11-15",  # Gurunanak Jayanti
        "2024-12-25",  # Christmas
    ]
    
    # NSE Holidays 2025
    HOLIDAYS_2025 = [
        "2025-01-26",  # Republic Day
        "2025-02-26",  # Maha Shivaratri
        "2025-03-14",  # Holi
        "2025-03-31",  # Id-Ul-Fitr (Ramadan)
        "2025-04-10",  # Mahavir Jayanti
        "2025-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
        "2025-04-18",  # Good Friday
        "2025-05-01",  # Maharashtra Day
        "2025-05-12",  # Buddha Purnima
        "2025-06-07",  # Eid ul-Adha (Bakrid)
        "2025-07-06",  # Muharram
        "2025-08-15",  # Independence Day
        "2025-08-16",  # Janmashtami
        "2025-10-02",  # Mahatma Gandhi Jayanti
        "2025-10-21",  # Diwali Laxmi Pujan
        "2025-11-05",  # Gurunanak Jayanti
        "2025-12-25",  # Christmas
    ]

    # NSE Holidays 2026 — source: NSE India official circular
    HOLIDAYS_2026 = [
        "2026-01-15",  # Municipal Corporation Election - Maharashtra
        "2026-01-26",  # Republic Day
        "2026-03-03",  # Holi
        "2026-03-26",  # Shri Ram Navami
        "2026-03-31",  # Shri Mahavir Jayanti
        "2026-04-03",  # Good Friday
        "2026-04-14",  # Dr. Baba Saheb Ambedkar Jayanti
        "2026-05-01",  # Maharashtra Day
        "2026-05-28",  # Bakri Id
        "2026-06-26",  # Muharram
        "2026-09-14",  # Ganesh Chaturthi
        "2026-10-02",  # Mahatma Gandhi Jayanti
        "2026-10-20",  # Dussehra
        "2026-11-08",  # Diwali Laxmi Pujan
        "2026-11-10",  # Diwali-Balipratipada
        "2026-11-24",  # Prakash Gurpurb Sri Guru Nanak Dev
        "2026-12-25",  # Christmas
    ]

    _HOLIDAY_CACHE_PATH = "/tmp/nse_holidays_cache.json"
    _HOLIDAY_CACHE_TTL  = 86_400  # 24 hours

    def __init__(self):
        """Initialize the market calendar with dynamic + static holiday data."""
        static: Set[str] = set(self.HOLIDAYS_2024 + self.HOLIDAYS_2025 + self.HOLIDAYS_2026)
        dynamic = self._fetch_dynamic_holidays()
        self._holidays: Set[str] = static | dynamic

        # Allow overriding specific dates as trading days via env var.
        # Useful when NSE API mistakenly lists clearing-only holidays as trading holidays.
        # Format: comma-separated YYYY-MM-DD dates, e.g. "2026-03-19,2026-04-01"
        extra_trading = os.getenv("NSE_EXTRA_TRADING_DATES", "")
        if extra_trading:
            for d in extra_trading.split(","):
                d = d.strip()
                if d:
                    self._holidays.discard(d)
                    logger.debug(f"NSE_EXTRA_TRADING_DATES: forcing {d} as a trading day")

        logger.debug(
            f"Loaded {len(self._holidays)} holidays "
            f"(static={len(static)}, dynamic_additions={len(dynamic - static)})"
        )

    # ── Dynamic holiday fetch ────────────────────────────────────────────────

    def _fetch_dynamic_holidays(self) -> Set[str]:
        """
        Try to fetch the official NSE holiday list and cache it for 24 h.
        Returns an empty set on any failure — the static list is the safety net.
        """
        cached = self._load_cache()
        if cached is not None:
            return cached

        try:
            import requests
            headers = {
                "User-Agent": (
                    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
                    "AppleWebKit/537.36 (KHTML, like Gecko) "
                    "Chrome/120.0.0.0 Safari/537.36"
                ),
                "Accept": "application/json, text/plain, */*",
                "Accept-Language": "en-US,en;q=0.9",
                "Referer": "https://www.nseindia.com/",
            }
            session = requests.Session()
            session.get("https://www.nseindia.com", headers=headers, timeout=3)
            resp = session.get(
                "https://www.nseindia.com/api/holiday-master?type=trading",
                headers=headers,
                timeout=3,
            )
            if resp.status_code != 200:
                logger.debug(f"NSE holiday API returned {resp.status_code}; using static list")
                return set()

            data = resp.json()
            holidays: Set[str] = set()
            for month_entries in data.values():
                if not isinstance(month_entries, list):
                    continue
                for entry in month_entries:
                    raw_date = entry.get("tradingDate") or entry.get("trading_date")
                    if not raw_date:
                        continue
                    for fmt in ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y"):
                        try:
                            dt = datetime.strptime(str(raw_date).strip(), fmt)
                            holidays.add(dt.strftime("%Y-%m-%d"))
                            break
                        except ValueError:
                            continue

            if holidays:
                self._save_cache(holidays)
                logger.info(f"Fetched {len(holidays)} NSE holidays dynamically")
            return holidays

        except Exception as e:
            logger.debug(f"Dynamic holiday fetch failed (using static list): {e}")
            return set()

    def _load_cache(self) -> Optional[Set[str]]:
        try:
            if not os.path.exists(self._HOLIDAY_CACHE_PATH):
                return None
            if time.time() - os.path.getmtime(self._HOLIDAY_CACHE_PATH) > self._HOLIDAY_CACHE_TTL:
                return None
            with open(self._HOLIDAY_CACHE_PATH) as f:
                return set(json.load(f))
        except Exception:
            return None

    def _save_cache(self, holidays: Set[str]) -> None:
        try:
            with open(self._HOLIDAY_CACHE_PATH, "w") as f:
                json.dump(sorted(holidays), f)
        except Exception as e:
            logger.debug(f"Could not save holiday cache: {e}")
    
    def _now_ist(self) -> datetime:
        """Get current time in IST."""
        return datetime.now(IST)
    
    def _today_ist(self) -> date:
        """Get today's date in IST."""
        return self._now_ist().date()
    
    def is_weekend(self, check_date: Optional[date] = None) -> bool:
        """
        Check if a date is a weekend (Saturday or Sunday).
        
        Args:
            check_date: Date to check (defaults to today IST)
        
        Returns:
            True if weekend, False otherwise
        """
        if check_date is None:
            check_date = self._today_ist()
        
        # Monday = 0, Sunday = 6
        return check_date.weekday() >= 5
    
    def is_holiday(self, check_date: Optional[date] = None) -> bool:
        """
        Check if a date is an NSE holiday.
        
        Args:
            check_date: Date to check (defaults to today IST)
        
        Returns:
            True if holiday, False otherwise
        """
        if check_date is None:
            check_date = self._today_ist()
        
        date_str = check_date.strftime("%Y-%m-%d")
        return date_str in self._holidays
    
    def is_holiday_today(self) -> bool:
        """Check if today is an NSE holiday."""
        return self.is_holiday(self._today_ist())
    
    def is_trading_day(self, check_date: Optional[date] = None) -> bool:
        """
        Check if a date is a valid trading day (not weekend, not holiday).
        
        Args:
            check_date: Date to check (defaults to today IST)
        
        Returns:
            True if trading day, False otherwise
        """
        if check_date is None:
            check_date = self._today_ist()
        
        return not self.is_weekend(check_date) and not self.is_holiday(check_date)
    
    def is_market_hours(self) -> bool:
        """
        Check if current time is within NSE trading hours (9:15 AM - 3:30 PM IST).
        
        Returns:
            True if within trading hours, False otherwise
        """
        now = self._now_ist()
        
        market_open = now.replace(
            hour=self.MARKET_OPEN_HOUR,
            minute=self.MARKET_OPEN_MINUTE,
            second=0,
            microsecond=0
        )
        market_close = now.replace(
            hour=self.MARKET_CLOSE_HOUR,
            minute=self.MARKET_CLOSE_MINUTE,
            second=0,
            microsecond=0
        )
        
        return market_open <= now <= market_close
    
    def is_pre_market(self) -> bool:
        """
        Check if current time is in pre-market session (9:00 AM - 9:15 AM IST).
        
        Returns:
            True if in pre-market session, False otherwise
        """
        now = self._now_ist()
        
        pre_market_start = now.replace(
            hour=self.PRE_MARKET_START_HOUR,
            minute=self.PRE_MARKET_START_MINUTE,
            second=0,
            microsecond=0
        )
        market_open = now.replace(
            hour=self.MARKET_OPEN_HOUR,
            minute=self.MARKET_OPEN_MINUTE,
            second=0,
            microsecond=0
        )
        
        return pre_market_start <= now < market_open
    
    def is_market_open(self) -> bool:
        """
        Check if the market is currently open for trading.
        Combines trading day check and trading hours check.
        
        Returns:
            True if market is open, False otherwise
        """
        return self.is_trading_day() and self.is_market_hours()
    
    def time_to_market_open(self) -> Optional[timedelta]:
        """
        Calculate time until market opens.
        
        Returns:
            timedelta until market opens, or None if market is already open
        """
        if self.is_market_open():
            return None
        
        now = self._now_ist()
        
        # If before market open today
        today_open = now.replace(
            hour=self.MARKET_OPEN_HOUR,
            minute=self.MARKET_OPEN_MINUTE,
            second=0,
            microsecond=0
        )
        
        if now < today_open and self.is_trading_day():
            return today_open - now
        
        # Otherwise, calculate to next trading day
        next_day = self.next_trading_day()
        if next_day:
            next_open = datetime.combine(
                next_day,
                datetime.min.time().replace(
                    hour=self.MARKET_OPEN_HOUR,
                    minute=self.MARKET_OPEN_MINUTE
                )
            )
            next_open = IST.localize(next_open)
            return next_open - now
        
        return None
    
    def time_to_market_close(self) -> Optional[timedelta]:
        """
        Calculate time until market closes.
        
        Returns:
            timedelta until market closes, or None if market is already closed
        """
        if not self.is_market_open():
            return None
        
        now = self._now_ist()
        today_close = now.replace(
            hour=self.MARKET_CLOSE_HOUR,
            minute=self.MARKET_CLOSE_MINUTE,
            second=0,
            microsecond=0
        )
        
        return today_close - now
    
    def next_trading_day(self, from_date: Optional[date] = None) -> Optional[date]:
        """
        Get the next valid trading day.
        
        Args:
            from_date: Start date (defaults to today IST)
        
        Returns:
            Next trading day, or None if not found within 30 days
        """
        if from_date is None:
            from_date = self._today_ist()
        
        # Search up to 30 days ahead
        for i in range(1, 31):
            check_date = from_date + timedelta(days=i)
            if self.is_trading_day(check_date):
                return check_date
        
        return None
    
    def previous_trading_day(self, from_date: Optional[date] = None) -> Optional[date]:
        """
        Get the previous valid trading day.
        
        Args:
            from_date: Start date (defaults to today IST)
        
        Returns:
            Previous trading day, or None if not found within 30 days
        """
        if from_date is None:
            from_date = self._today_ist()
        
        # Search up to 30 days back
        for i in range(1, 31):
            check_date = from_date - timedelta(days=i)
            if self.is_trading_day(check_date):
                return check_date
        
        return None
    
    def trading_days_between(self, start_date: date, end_date: date) -> List[date]:
        """
        Get all trading days between two dates (inclusive).
        
        Args:
            start_date: Start date
            end_date: End date
        
        Returns:
            List of trading days
        """
        trading_days = []
        current = start_date
        
        while current <= end_date:
            if self.is_trading_day(current):
                trading_days.append(current)
            current += timedelta(days=1)
        
        return trading_days
    
    def get_market_status(self) -> dict:
        """
        Get comprehensive market status information.
        
        Returns:
            Dictionary with market status details
        """
        now = self._now_ist()
        is_open = self.is_market_open()
        
        status = {
            "timestamp": now.isoformat(),
            "is_trading_day": self.is_trading_day(),
            "is_weekend": self.is_weekend(),
            "is_holiday": self.is_holiday(),
            "is_market_hours": self.is_market_hours(),
            "is_pre_market": self.is_pre_market(),
            "is_market_open": is_open,
        }
        
        if is_open:
            time_to_close = self.time_to_market_close()
            status["time_to_close"] = str(time_to_close) if time_to_close else None
            status["message"] = f"Market is OPEN. Closes in {time_to_close}"
        else:
            time_to_open = self.time_to_market_open()
            # If today is a trading day but market hasn't opened yet, today IS the next trading day
            today = self._today_ist()
            today_open = now.replace(
                hour=self.MARKET_OPEN_HOUR,
                minute=self.MARKET_OPEN_MINUTE,
                second=0,
                microsecond=0,
            )
            if self.is_trading_day(today) and now < today_open:
                next_day = today
            else:
                next_day = self.next_trading_day()
            status["time_to_open"] = str(time_to_open) if time_to_open else None
            status["next_trading_day"] = next_day.isoformat() if next_day else None
            
            if self.is_weekend():
                status["message"] = f"Market CLOSED (weekend). Next trading day: {next_day}"
            elif self.is_holiday():
                status["message"] = f"Market CLOSED (holiday). Next trading day: {next_day}"
            else:
                status["message"] = f"Market CLOSED. Opens in {time_to_open}"
        
        return status


# Singleton instance for convenience
_calendar_instance = None


def get_market_calendar() -> NSEMarketCalendar:
    """Get singleton market calendar instance."""
    global _calendar_instance
    if _calendar_instance is None:
        _calendar_instance = NSEMarketCalendar()
    return _calendar_instance


# Utility functions for quick access
def is_market_open() -> bool:
    """Quick check if market is open."""
    return get_market_calendar().is_market_open()


def is_trading_day(check_date: Optional[date] = None) -> bool:
    """Quick check if a date is a trading day."""
    return get_market_calendar().is_trading_day(check_date)


def should_run_workflow() -> bool:
    """
    Check if the trading workflow should run.
    Returns True if it's a trading day (regardless of market hours).
    
    Use this for analysis workflows that prepare for trading.
    """
    return get_market_calendar().is_trading_day()


def should_execute_trades() -> bool:
    """
    Check if trades should be executed.
    Returns True only if market is currently open.
    
    Use this for actual trade execution.
    """
    return get_market_calendar().is_market_open()


if __name__ == "__main__":
    # Test the calendar
    calendar = NSEMarketCalendar()
    status = calendar.get_market_status()
    
    print("\n" + "=" * 50)
    print("NSE Market Status")
    print("=" * 50)
    
    for key, value in status.items():
        print(f"  {key}: {value}")
    
    print("\n" + "=" * 50)
    print("Upcoming Trading Days")
    print("=" * 50)
    
    today = calendar._today_ist()
    for i in range(7):
        check_date = today + timedelta(days=i)
        is_trading = calendar.is_trading_day(check_date)
        day_name = check_date.strftime("%A")
        status_str = "TRADING" if is_trading else "CLOSED"
        
        if calendar.is_weekend(check_date):
            reason = "(weekend)"
        elif calendar.is_holiday(check_date):
            reason = "(holiday)"
        else:
            reason = ""
        
        print(f"  {check_date} ({day_name}): {status_str} {reason}")
