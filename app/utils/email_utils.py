import os
import smtplib
import dns.resolver
from email.mime.text import MIMEText
from email.mime.multipart import MIMEMultipart
from dotenv import load_dotenv
from fastapi import HTTPException, status

# Load environment variables
load_dotenv()

# Email configuration
EMAIL_HOST = os.getenv("EMAIL_HOST", "smtp.gmail.com")
EMAIL_PORT = int(os.getenv("EMAIL_PORT", "587"))
EMAIL_USERNAME = os.getenv("EMAIL_USERNAME")
EMAIL_PASSWORD = os.getenv("EMAIL_PASSWORD")
EMAIL_FROM = os.getenv("EMAIL_FROM")
SITE_NAME = os.getenv("SITE_NAME", "IELTS Practice")

# Print debug information
print(f"Email Configuration Debug:")
print(f"EMAIL_HOST: {EMAIL_HOST}")
print(f"EMAIL_PORT: {EMAIL_PORT}")
print(f"EMAIL_USERNAME: {'Set' if EMAIL_USERNAME else 'Not set'}")
print(f"EMAIL_PASSWORD: {'Set' if EMAIL_PASSWORD else 'Not set'}")
print(f"EMAIL_FROM: {EMAIL_FROM}")

def is_valid_email(email: str) -> bool:
    """
    Check if an email address is potentially valid by verifying the domain's MX records.
    
    Args:
        email: The email address to validate
        
    Returns:
        bool: True if the email domain has valid MX records, False otherwise
    """
    try:
        # Split the email address to get the domain
        domain = email.split('@')[1]
        
        # Check if MX records exist for the domain
        dns.resolver.resolve(domain, 'MX')
        return True
    except (IndexError, dns.resolver.NoAnswer, dns.resolver.NXDOMAIN, dns.exception.Timeout):
        return False
    except Exception as e:
        print(f"Error validating email domain: {str(e)}")
        # Return True in case of other errors to avoid blocking registration
        return True

def send_email(to_email: str, subject: str, html_content: str) -> bool:
    """
    Send an email with the given subject and HTML content to the specified recipient.
    
    Args:
        to_email: Email address of the recipient
        subject: Subject line of the email
        html_content: HTML content of the email body
        
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    if not EMAIL_USERNAME or not EMAIL_PASSWORD:
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="Email service is not configured properly"
        )
    
    # Create message
    message = MIMEMultipart("alternative")
    message["Subject"] = subject
    message["From"] = EMAIL_FROM or EMAIL_USERNAME
    message["To"] = to_email
    
    # Attach HTML content
    html_part = MIMEText(html_content, "html")
    message.attach(html_part)
    
    try:
        # Connect to SMTP server
        server = smtplib.SMTP(EMAIL_HOST, EMAIL_PORT)
        server.starttls()
        server.login(EMAIL_USERNAME, EMAIL_PASSWORD)
        
        # Send email
        server.sendmail(EMAIL_USERNAME, to_email, message.as_string())
        server.quit()
        return True
    except Exception as e:
        print(f"Failed to send email: {str(e)}")
        return False

def send_password_reset_email(to_email: str, reset_token: str, username: str, frontend_url: str) -> bool:
    """
    Send a password reset email with a reset link to the user.
    
    Args:
        to_email: Email address of the user
        reset_token: The token to be used for password reset
        username: The username of the user
        frontend_url: The base URL of the frontend application
        
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    # Create reset link
    reset_link = f"{frontend_url}/reset-password?token={reset_token}"
    
       # Email content
    subject = f"{SITE_NAME} - Đặt Lại Mật Khẩu"
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 5px;">
            <h2 style="color: #4a86e8;">{SITE_NAME} - Đặt Lại Mật Khẩu</h2>
            <p>Xin chào {username},</p>
            <p>Chúng tôi đã nhận được yêu cầu đặt lại mật khẩu cho tài khoản {SITE_NAME} của bạn.</p>
            <p>Để đặt lại mật khẩu, vui lòng nhấp vào liên kết dưới đây:</p>
            <p>
                <a href="{reset_link}" style="display: inline-block; padding: 10px 20px; background-color: #4a86e8; color: white; text-decoration: none; border-radius: 5px;">
                    Đặt Lại Mật Khẩu
                </a>
            </p>
            <p>Liên kết này sẽ hết hạn sau 30 phút.</p>
            <p>Nếu bạn không yêu cầu đặt lại mật khẩu, vui lòng bỏ qua email này hoặc liên hệ với bộ phận hỗ trợ nếu bạn có thắc mắc.</p>
            <p>Xin cảm ơn,<br>Đội ngũ {SITE_NAME}</p>
        </div>
    </body>
    </html>
    """ 
    return send_email(to_email, subject, html_content)

def send_account_created_email(to_email: str, username: str, frontend_url: str) -> bool:
    """
    Send an email notifying a user that an account has been created with their email.
    
    Args:
        to_email: Email address of the user
        username: The username of the user
        frontend_url: The base URL of the frontend application
        
    Returns:
        bool: True if email was sent successfully, False otherwise
    """
    # Create login link
    login_link = f"{frontend_url}/login"
    
    # Email content
        # Email content
    subject = f"{SITE_NAME} - Tài Khoản Đã Được Tạo"
    html_content = f"""
    <html>
    <body style="font-family: Arial, sans-serif; line-height: 1.6; color: #333;">
        <div style="max-width: 600px; margin: 0 auto; padding: 20px; border: 1px solid #eee; border-radius: 5px;">
            <h2 style="color: #4a86e8;">Chào mừng đến với {SITE_NAME}!</h2>
            <p>Xin chào {username},</p>
            <p>Chúng tôi rất vui mừng thông báo rằng tài khoản {SITE_NAME} của bạn đã được tạo thành công.</p>
            <p>Bạn có thể đăng nhập ngay bây giờ để truy cập tất cả các tài nguyên luyện thi IELTS của chúng tôi.</p>
            <p>
                <a href="{login_link}" style="display: inline-block; padding: 10px 20px; background-color: #4a86e8; color: white; text-decoration: none; border-radius: 5px;">
                    Đăng Nhập Ngay
                </a>
            </p>
            <p>Nếu bạn có bất kỳ câu hỏi hoặc cần hỗ trợ, vui lòng liên hệ với đội ngũ hỗ trợ của chúng tôi.</p>
            <p>Xin cảm ơn,<br>Đội ngũ {SITE_NAME}</p>
        </div>
    </body>
    </html>
    """
    
    return send_email(to_email, subject, html_content) 
