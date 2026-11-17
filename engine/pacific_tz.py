"""US Pacific time without the tzdata package.

Alibaba ads report days are US Pacific dates. macOS/Linux Python finds the
system zone database; Windows Python has none unless the `tzdata` package is
installed, so fall back to the US DST rule (since 2007): PDT from the second
Sunday of March 02:00 local to the first Sunday of November 02:00 local.
"""
import datetime as dt

_STD, _DST = dt.timedelta(hours=-8), dt.timedelta(hours=-7)


def _sunday(year, month, nth):
    d = dt.date(year, month, 1)
    d += dt.timedelta(days=(6 - d.weekday()) % 7)
    return d + dt.timedelta(weeks=nth - 1)


def _dst_utc_range(year):
    """(start, end) of PDT in naive UTC."""
    start = dt.datetime.combine(_sunday(year, 3, 2), dt.time(2)) - _STD
    end = dt.datetime.combine(_sunday(year, 11, 1), dt.time(2)) - _DST
    return start, end


class USPacific(dt.tzinfo):
    def _is_dst_local(self, local):
        start = dt.datetime.combine(_sunday(local.year, 3, 2), dt.time(2))
        end = dt.datetime.combine(_sunday(local.year, 11, 1), dt.time(1))
        naive = local.replace(tzinfo=None)
        if start <= naive < end:
            return True
        if end <= naive < end + dt.timedelta(hours=1):  # repeated hour: fold 0 = PDT
            return not local.fold
        return False

    def utcoffset(self, d):
        return _DST if d is not None and self._is_dst_local(d) else _STD

    def dst(self, d):
        return _DST - _STD if d is not None and self._is_dst_local(d) else dt.timedelta(0)

    def tzname(self, d):
        return "PDT" if d is not None and self._is_dst_local(d) else "PST"

    def fromutc(self, d):
        naive = d.replace(tzinfo=None)
        start, end = _dst_utc_range(naive.year)
        if start <= naive < end:
            return (naive + _DST).replace(tzinfo=self)
        local = (naive + _STD).replace(tzinfo=self)
        if end <= naive < end + dt.timedelta(hours=1):
            local = local.replace(fold=1)
        return local


def pacific():
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo("America/Los_Angeles")
    except Exception:  # zoneinfo raises ZoneInfoNotFoundError when no zone database exists
        return USPacific()
