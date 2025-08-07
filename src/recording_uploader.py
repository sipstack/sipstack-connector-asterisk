"""Recording uploader that runs periodically."""

import asyncio
import logging
import os
import subprocess
from datetime import datetime
from typing import Optional

logger = logging.getLogger(__name__)


class RecordingUploader:
    """Handles periodic recording uploads using the bash script."""
    
    def __init__(self, interval_seconds: int = 60):
        """
        Initialize the recording uploader.
        
        Args:
            interval_seconds: How often to run the upload script (default: 60 seconds)
        """
        self.interval_seconds = interval_seconds
        self._running = False
        self._task: Optional[asyncio.Task] = None
        self.script_path = "/app/scripts/upload-recordings.sh"
        
    async def start(self):
        """Start the periodic upload task."""
        if self._running:
            logger.warning("Recording uploader already running")
            return
            
        self._running = True
        self._task = asyncio.create_task(self._run_periodic())
        logger.info(f"Recording uploader started - will run every {self.interval_seconds} seconds")
        
    async def stop(self):
        """Stop the periodic upload task."""
        self._running = False
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
        logger.info("Recording uploader stopped")
        
    async def _run_periodic(self):
        """Run the upload script periodically."""
        while self._running:
            try:
                await self._run_upload_script()
            except Exception as e:
                logger.error(f"Error running upload script: {e}", exc_info=True)
                
            # Wait for the next interval
            try:
                await asyncio.sleep(self.interval_seconds)
            except asyncio.CancelledError:
                break
                
    async def _run_upload_script(self):
        """Execute the upload script."""
        if not os.path.exists(self.script_path):
            logger.error(f"Upload script not found: {self.script_path}")
            return
            
        logger.debug("Running recording upload script")
        start_time = datetime.now()
        
        try:
            # Run the script asynchronously
            process = await asyncio.create_subprocess_exec(
                self.script_path,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                env=os.environ.copy()
            )
            
            stdout, stderr = await process.communicate()
            
            duration = (datetime.now() - start_time).total_seconds()
            
            if process.returncode == 0:
                logger.info(f"Upload script completed successfully in {duration:.2f}s")
                if stdout:
                    # Log first few lines at INFO level for visibility
                    lines = stdout.decode().strip().split('\n')
                    if lines:
                        logger.info(f"Script output summary: {lines[0]}")
                        if len(lines) > 1:
                            logger.info(f"... processed {len(lines)} recordings")
            else:
                logger.error(f"Upload script failed with exit code {process.returncode}")
                if stderr:
                    logger.error(f"Script error: {stderr.decode().strip()}")
                    
        except Exception as e:
            logger.error(f"Failed to run upload script: {e}", exc_info=True)