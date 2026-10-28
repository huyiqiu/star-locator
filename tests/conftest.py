import os
from pathlib import Path
import sys
import tempfile
import cv2
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
cv2.setNumThreads(2)

@pytest.fixture(scope='session')
def client():
    from fastapi.testclient import TestClient
    with tempfile.TemporaryDirectory() as tmp:
        os.environ['STAR16_DATA_DIR']=tmp
        from backend.app import app
        with TestClient(app) as session:
            yield session
