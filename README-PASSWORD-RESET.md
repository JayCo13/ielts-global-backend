# Password Reset Functionality

This document explains how to set up and use the password reset functionality in the IELTS Practice Backend.

## Setup

1. Update your `.env` file with the following email configuration:

```
# Email Configuration
EMAIL_HOST=smtp.gmail.com
EMAIL_PORT=587
EMAIL_USERNAME=your_email@gmail.com
EMAIL_PASSWORD=your_app_password
EMAIL_FROM=your_email@gmail.com
```

Notes for Gmail:
- You'll need to create an "App Password" for EMAIL_PASSWORD if you're using Gmail. 
- Go to your Google Account → Security → 2-Step Verification → App passwords
- More info: https://support.google.com/accounts/answer/185833

2. Make sure you have the following packages installed:
```
pip install python-dotenv email-validator
```

## Available Endpoints

The following endpoints are available for password reset:

### 1. Request Password Reset

**Endpoint:** `/auth/request-password-reset`  
**Method:** POST  
**Request Body:**
```json
{
  "email": "user@example.com"
}
```
**Response:**
```json
{
  "message": "If the email exists in our system, a password reset link will be sent."
}
```

### 2. Verify Reset Token

**Endpoint:** `/auth/verify-reset-token`  
**Method:** GET  
**Query Parameters:**
- `token`: The reset token from the email

**Response (valid token):**
```json
{
  "valid": true,
  "username": "username",
  "email": "user@example.com"
}
```

**Response (invalid token):**
```json
{
  "valid": false
}
```

### 3. Reset Password

**Endpoint:** `/auth/reset-password`  
**Method:** POST  
**Request Body:**
```json
{
  "token": "reset_token_from_email",
  "new_password": "new_password",
  "confirm_password": "new_password"
}
```
**Response:**
```json
{
  "message": "Password has been reset successfully"
}
```

## Frontend Integration

The password reset flow typically works as follows:

1. User requests a password reset by providing their email
2. System sends an email with a reset link
3. User clicks the link, which takes them to a reset password page
4. Frontend verifies the token
5. User enters a new password
6. System resets the password

The link sent to users will be in the format:
```
{FRONTEND_URL}/reset-password?token={TOKEN}
```

Your frontend application should handle this route and implement the appropriate UI for password reset.

## Troubleshooting

If you experience issues with sending emails:

1. Check your email provider settings
2. For Gmail, make sure "Less secure app access" is enabled or use App Passwords
3. Check spam/junk folders if emails are not being received
4. Verify that all environment variables are set correctly 