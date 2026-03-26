import pytz
from datetime import datetime, timedelta

def get_vietnam_time():
    """Get current datetime in Vietnam timezone"""
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    return datetime.now(vietnam_tz)

def convert_to_vietnam_time(dt):
    """Convert a datetime object to Vietnam timezone"""
    if dt is None:
        return None
        
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=pytz.UTC)
    vietnam_tz = pytz.timezone('Asia/Ho_Chi_Minh')
    return dt.astimezone(vietnam_tz)

def format_vietnam_time(dt, format_str="%Y-%m-%d %H:%M:%S"):
    """Format a datetime object in Vietnam timezone"""
    if dt is None:
        return None
        
    vn_time = convert_to_vietnam_time(dt)
    return vn_time.strftime(format_str)