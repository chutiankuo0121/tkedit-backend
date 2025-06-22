# 媒体分析器 (ffmpeg封装)
import asyncio
import json
import logging
import os
import platform
import subprocess
from typing import Optional, Dict, Any

logger = logging.getLogger(__name__)

# --- Path Configuration ---
PROJECT_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
FFPROBE_EXE_NAME = "ffprobe.exe" if platform.system() == "Windows" else "ffprobe"

# 优先使用系统路径中的ffprobe（Docker容器中通过apt安装），降级到本地路径
def get_ffprobe_path():
    import shutil
    # 首先尝试系统PATH中的ffprobe
    system_ffprobe = shutil.which("ffprobe")
    if system_ffprobe:
        return system_ffprobe
    # 降级到项目本地路径
    return os.path.join(PROJECT_ROOT, 'ffmpeg', 'bin', FFPROBE_EXE_NAME)

FFPROBE_PATH = get_ffprobe_path()

# --- MediaAnalysisResult Class ---
class MediaAnalysisResult:
    def __init__(self, analysis: Dict[str, Any]):
        self._analysis = analysis
        self._format = analysis.get('format', {})
        self._video_stream = self._get_stream_by_codec_type('video')
        self._audio_stream = self._get_stream_by_codec_type('audio')

    def _get_stream_by_codec_type(self, codec_type: str) -> Optional[Dict[str, Any]]:
        return next((s for s in self._analysis.get('streams', []) if s.get('codec_type') == codec_type), None)

    @property
    def duration_us(self) -> int:
        duration_s = self._format.get('duration', '0')
        try:
            return int(float(duration_s) * 1_000_000)
        except (ValueError, TypeError):
            return 0

    @property
    def width(self) -> Optional[int]:
        return self._video_stream.get('width') if self._video_stream else None

    @property
    def height(self) -> Optional[int]:
        return self._video_stream.get('height') if self._video_stream else None

    @property
    def fps(self) -> Optional[float]:
        if not self._video_stream or 'avg_frame_rate' not in self._video_stream:
            return None
        rate_str = self._video_stream['avg_frame_rate']
        if '/' in rate_str:
            num, den = map(int, rate_str.split('/'))
            return num / den if den != 0 else 0.0
        return float(rate_str)

    @property
    def sample_rate(self) -> Optional[int]:
        if self._audio_stream and 'sample_rate' in self._audio_stream:
            return int(self._audio_stream['sample_rate'])
        return None

# --- REWRITTEN MediaAnalyzer Class ---
class MediaAnalyzer:
    def __init__(self, ffprobe_path: str = FFPROBE_PATH):
        self.ffprobe_path = ffprobe_path
        if not os.path.exists(self.ffprobe_path):
            raise FileNotFoundError(
                f"ffprobe executable not found at the configured path: {self.ffprobe_path}. "
                "Please ensure ffmpeg is correctly placed in the project root directory."
            )

    def _analyze_sync(self, file_path: str) -> Dict[str, Any]:
        """
        A synchronous, blocking function that runs ffprobe.
        This is designed to be executed in a thread pool to avoid blocking the server.
        """
        normalized_path = file_path.replace('\\', '/')
        command = [
            self.ffprobe_path,
            '-v', 'error',
            '-print_format', 'json',
            '-show_format',
            '-show_streams',
            normalized_path
        ]
        

        try:
            result = subprocess.run(
                command,
                capture_output=True,
                text=True,
                check=True,
                encoding='utf-8'
            )
            
            if not result.stdout.strip():
                 raise ValueError("ffprobe returned empty output, the file may be invalid.")
            
            return json.loads(result.stdout)

        except subprocess.CalledProcessError as e:
            error_message = e.stderr.strip()
            raise RuntimeError(f"ffprobe failed: {error_message}") from e
        except json.JSONDecodeError as e:
            return None
        except Exception as e:
            return None

    async def analyze(self, file_path: str) -> Optional[MediaAnalysisResult]:
        """
        Asynchronously analyzes a media file by running the synchronous
        _analyze_sync method in a separate thread.
        """
        loop = asyncio.get_running_loop()
        try:
            parsed_analysis = await loop.run_in_executor(
                None, self._analyze_sync, file_path
            )
            return MediaAnalysisResult(parsed_analysis)
        except Exception as e:
            logger.error(f"Error during scheduled media analysis for {file_path}: {e}")
            raise

# Global instance
media_analyzer = MediaAnalyzer() 