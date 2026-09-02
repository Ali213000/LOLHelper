"""Smoke-test: verify all critical imports and CUDA availability."""
import sys
print(f"Python: {sys.version}")

print("\n--- PyTorch ---")
import torch
print(f"PyTorch:        {torch.__version__}")
print(f"CUDA available: {torch.cuda.is_available()}")
if torch.cuda.is_available():
    print(f"GPU:            {torch.cuda.get_device_name(0)}")
    print(f"CUDA version:   {torch.version.cuda}")
else:
    print("GPU:            N/A (CPU-only build)")

print("\n--- Vision ---")
import cv2
print(f"OpenCV:         {cv2.__version__}")
import mss
print(f"mss:            {mss.__version__}")

print("\n--- EasyOCR ---")
import easyocr
print(f"EasyOCR:        {easyocr.__version__}")

print("\n--- UI ---")
import customtkinter
print(f"CustomTkinter:  {customtkinter.__version__}")

print("\n--- LangChain ---")
import langchain
print(f"LangChain:      {langchain.__version__}")
import langchain_google_genai
print(f"LC-Gemini:      {langchain_google_genai.__version__}")

print("\n--- Other ---")
import httpx
print(f"httpx:          {httpx.__version__}")
import pynput
try:
    import importlib.metadata
    pynput_ver = importlib.metadata.version('pynput')
except Exception:
    pynput_ver = 'installed'
print(f"pynput:         {pynput_ver}")
import edge_tts
print(f"edge-tts:       {edge_tts.__version__}")

print("\n=== All imports OK ===")
