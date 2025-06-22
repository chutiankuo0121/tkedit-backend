# zip打包工具
import zipfile
import os
import logging
import asyncio
from typing import Optional

logger = logging.getLogger(__name__)

class ZipManager:
    """
    负责处理 ZIP 文件的打包操作。
    """
    async def create_zip_from_directory(self, source_dir: str, output_path: str) -> Optional[str]:
        """
        将指定目录的内容打包成一个ZIP文件。

        这是一个I/O密集型操作，所以我们使用 asyncio.to_thread 
        将其放在一个单独的线程中运行，以避免阻塞事件循环。

        Args:
            source_dir (str): 要压缩的源目录路径。
            output_path (str): 输出的ZIP文件路径 (包括文件名)。

        Returns:
            Optional[str]: 成功则返回ZIP文件的路径，否则返回 None。
        """
        if not os.path.isdir(source_dir):
            logger.error(f"Source directory for zipping does not exist: {source_dir}")
            return None

        try:
            # 确保输出目录存在
            output_dir = os.path.dirname(output_path)
            if not os.path.exists(output_dir):
                os.makedirs(output_dir)

            await asyncio.to_thread(self._zip_directory, source_dir, output_path)
            logger.info(f"Successfully created zip file at {output_path}")
            return output_path
        except Exception as e:
            logger.error(f"Failed to create zip file from {source_dir}: {e}")
            return None

    def _zip_directory(self, source_dir: str, output_path: str):
        """
        同步的压缩方法，将在线程池中执行。
        它会将源目录本身作为ZIP文件中的顶级文件夹。
        """
        archive_root_name = os.path.basename(os.path.normpath(source_dir))
        with zipfile.ZipFile(output_path, 'w', zipfile.ZIP_DEFLATED) as zipf:
            for root, dirs, files in os.walk(source_dir):
                # 计算在zip文件中的相对路径
                zip_dir_path = os.path.join(archive_root_name, os.path.relpath(root, source_dir))

                # 处理空目录的情况
                if not files and not dirs:
                    # 添加一个末尾带斜杠的条目来表示目录
                    zipf.writestr(zip_dir_path + '/', b'')

                # 将文件添加到zip中
                for file in files:
                    file_path = os.path.join(root, file)
                    zip_file_path = os.path.join(zip_dir_path, file)
                    zipf.write(file_path, zip_file_path)

# 创建一个全局可用的zip管理器实例
zip_manager = ZipManager() 