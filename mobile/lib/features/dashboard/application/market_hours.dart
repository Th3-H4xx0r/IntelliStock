/// True when [etNow] (wall-clock America/New_York) is within the US equity
/// regular session: Mon–Fri, 09:30–16:00. Holidays are not modeled (a known
/// limitation; acceptable for a "live" status hint).
bool isMarketOpenAtEt(DateTime etNow) {
  if (etNow.weekday == DateTime.saturday ||
      etNow.weekday == DateTime.sunday) {
    return false;
  }
  final minutes = etNow.hour * 60 + etNow.minute;
  const open = 9 * 60 + 30;
  const close = 16 * 60;
  return minutes >= open && minutes < close;
}

/// Convert a UTC instant to wall-clock ET. ET = UTC-4 (EDT) Apr–Oct, UTC-5
/// (EST) otherwise — approximated by month (no exact DST-boundary handling,
/// which is fine for an open/closed hint).
DateTime etFromUtc(DateTime utc) {
  final u = utc.toUtc();
  final isDst = u.month > 3 && u.month < 11; // Apr–Oct → EDT
  return u.subtract(Duration(hours: isDst ? 4 : 5));
}
