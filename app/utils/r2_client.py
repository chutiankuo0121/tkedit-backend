# R2存储客户端 
import aioboto3
from contextlib import asynccontextmanager
from botocore.exceptions import ClientError
from app.config import settings
import logging
from typing import Optional

class R2Client:
    def __init__(self):
        self.session = aioboto3.Session()
        self.bucket_name = settings.R2_BUCKET_NAME
        self.client_context = self.session.client(
            "s3",
            endpoint_url=settings.R2_ENDPOINT_URL,
            aws_access_key_id=settings.R2_ACCESS_KEY_ID,
            aws_secret_access_key=settings.R2_SECRET_ACCESS_KEY,
            region_name=settings.R2_REGION,
        )
        self.client = None

    @asynccontextmanager
    async def get_client(self):
        if self.client is None:
            self.client = await self.client_context.__aenter__()
        yield self.client
    
    async def close_client(self):
        if self.client:
            await self.client_context.__aexit__(None, None, None)
            self.client = None

    async def upload_fileobj(self, file_obj, object_key: str):
        """直接上传文件对象（文件流）"""
        async with self.get_client() as client:
            try:
                await client.upload_fileobj(file_obj, self.bucket_name, object_key)
                return True
            except Exception as e:
                logging.error(f"Failed to upload file stream to {object_key}: {e}")
                return False

    async def upload_file(self, file_path: str, object_key: str):
        """从本地文件路径上传"""
        async with self.get_client() as client:
            try:
                await client.upload_file(file_path, self.bucket_name, object_key)
                return True
            except Exception as e:
                logging.error(f"Failed to upload {file_path} to {object_key}: {e}")
                return False

    async def download_file(self, object_key: str, file_path: str):
        async with self.get_client() as client:
            try:
                await client.download_file(self.bucket_name, object_key, file_path)
            except ClientError as e:
                if e.response['Error']['Code'] == '404':
                    logging.warning(f"Error: Object {object_key} not found in bucket {self.bucket_name}.")
                else:
                    logging.error(f"An unexpected error occurred: {e}")
                raise
    
    async def check_connection(self):
        """
        检查与R2的连接是否正常，以及存储桶是否可访问。
        """
        async with self.get_client() as client:
            try:
                await client.list_objects_v2(Bucket=self.bucket_name, MaxKeys=1)
            except ClientError as e:
                error_code = e.response.get("Error", {}).get("Code")
                if error_code == "NoSuchBucket":
                    raise Exception(f"存储桶 '{self.bucket_name}' 不存在。")
                elif error_code in ["InvalidAccessKeyId", "SignatureDoesNotMatch"]:
                    raise Exception("R2 Access Key ID 或 Secret Access Key 无效。")
                else:
                    raise Exception(f"连接到 R2 存储桶时发生未知客户端错误: {e}")
            except Exception as e:
                raise Exception(f"无法连接到 R2 端点。请检查网络连接和 R2_ENDPOINT_URL: {e}")

    async def get_download_url(self, object_key: str, expires_in: int = 3600) -> Optional[str]:
        try:
            url = await self.client.generate_presigned_url(
                'get_object',
                Params={'Bucket': self.bucket_name, 'Key': object_key},
                ExpiresIn=expires_in
            )
            return url
        except Exception as e:
            logging.error(f"An unexpected error occurred: {e}")
            return None

# 创建一个全局可用的R2客户端实例
r2_client = R2Client() 