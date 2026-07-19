from storage.base import StorageAdapter


class S3StorageAdapter(StorageAdapter):
    """
    S3 / Cloudflare R2 存储适配器（未来实现）。
    当前为占位符，后续替换本地存储时实现。
    """

    def __init__(self, bucket: str, region: str, access_key: str, secret_key: str):
        self.bucket = bucket
        self.region = region
        self._client = None

    def _get_client(self):
        if self._client is None:
            import boto3
            self._client = boto3.client("s3", region_name=self.region)
        return self._client

    def save_bytes(self, path: str, content: bytes) -> str:
        self._get_client().put_object(Bucket=self.bucket, Key=path, Body=content)
        return f"s3://{self.bucket}/{path}"

    def save_text(self, path: str, content: str) -> str:
        return self.save_bytes(path, content.encode("utf-8"))

    def get_url(self, path: str) -> str:
        return f"https://{self.bucket}.s3.{self.region}.amazonaws.com/{path}"

    def exists(self, path: str) -> bool:
        try:
            self._get_client().head_object(Bucket=self.bucket, Key=path)
            return True
        except Exception:
            return False
