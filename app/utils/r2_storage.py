import boto3
import os
from botocore.config import Config

# R2 Configuration
R2_ACCOUNT_ID = os.getenv("R2_ACCOUNT_ID", "9927bda6471a3b2b936cb3d6f6778294")
R2_ACCESS_KEY_ID = os.getenv("R2_ACCESS_KEY_ID", "")
R2_SECRET_ACCESS_KEY = os.getenv("R2_SECRET_ACCESS_KEY", "")
R2_BUCKET_NAME = os.getenv("R2_BUCKET_NAME", "ielts-audio")
R2_PUBLIC_URL = os.getenv("R2_PUBLIC_URL", "https://pub-c88afd65efbc4f2384083f8c557b8aff.r2.dev")

# S3-compatible endpoint for R2
R2_ENDPOINT = f"https://{R2_ACCOUNT_ID}.r2.cloudflarestorage.com"


def get_r2_client():
    """Create and return an S3-compatible client for Cloudflare R2."""
    return boto3.client(
        "s3",
        endpoint_url=R2_ENDPOINT,
        aws_access_key_id=R2_ACCESS_KEY_ID,
        aws_secret_access_key=R2_SECRET_ACCESS_KEY,
        config=Config(signature_version="s3v4"),
        region_name="auto",
    )


def upload_audio_to_r2(file_content: bytes, filename: str, content_type: str = "audio/mpeg") -> str:
    """
    Upload audio file to R2 and return the public URL.
    
    Args:
        file_content: Binary content of the audio file
        filename: Name/key for the file in R2 (e.g., 'section_5.mp3')
        content_type: MIME type of the file
    
    Returns:
        Public URL of the uploaded file
    """
    client = get_r2_client()
    
    key = f"audio/{filename}"
    
    client.put_object(
        Bucket=R2_BUCKET_NAME,
        Key=key,
        Body=file_content,
        ContentType=content_type,
    )
    
    return f"{R2_PUBLIC_URL}/{key}"


def delete_audio_from_r2(filename: str) -> bool:
    """Delete an audio file from R2."""
    try:
        client = get_r2_client()
        key = f"audio/{filename}"
        client.delete_object(Bucket=R2_BUCKET_NAME, Key=key)
        return True
    except Exception as e:
        print(f"Error deleting from R2: {e}")
        return False
