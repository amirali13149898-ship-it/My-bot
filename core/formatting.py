"""توابع فرمت‌بندی متن (حجم، سرعت، نوار پیشرفت)"""



def _format_mb(n):
    return f"{(n or 0) / (1024 * 1024):.1f}MB"


def _format_speed(bytes_per_sec):
    if bytes_per_sec >= 1024 * 1024:
        return f"{bytes_per_sec / (1024 * 1024):.1f}MB/s"
    return f"{bytes_per_sec / 1024:.0f}KB/s"


def _format_duration(seconds):
    seconds = int(max(0, seconds))
    if seconds < 60:
        return f"{seconds} ثانیه"
    minutes, secs = divmod(seconds, 60)
    if minutes < 60:
        return f"{minutes} دقیقه" + (f" و {secs} ثانیه" if secs else "")
    hours, minutes = divmod(minutes, 60)
    return f"{hours} ساعت" + (f" و {minutes} دقیقه" if minutes else "")


def _progress_bar(done, total, width=14):
    if not total:
        return "░" * width
    pct = max(0.0, min(1.0, done / total))
    filled = int(width * pct)
    return "▓" * filled + "░" * (width - filled)


def _build_upload_text(prefix, done, total, speed):
    done = min(done, total) if total else done
    pct = min(100, int(done * 100 / total)) if total else 0
    lines = [
        prefix,
        f"[{_progress_bar(done, total)}] {pct}%",
        f"{_format_mb(done)} / {_format_mb(total)}",
    ]
    if speed > 0:
        eta = (total - done) / speed
        lines.append(f"🚀 {_format_speed(speed)}  •  ⏳ {_format_duration(eta)} مانده")
    else:
        lines.append("🚀 در حال محاسبه سرعت...")
    return "\n".join(lines)
