"""
Utility modules for GUI Automation Agent.

This package provides:
- ComputerTools: Desktop GUI automation
- StepPopup: Blocking popup dialogs
- TemplateMatcher: OpenCV-based template matching
- Image/vision utilities: screenshot annotation, resizing
- Parsers: tool call extraction
- LLM wrappers: VLM API clients
"""

# # Computer interaction
# from utils.computer_tools import ComputerTools

# # Popup dialog
# from utils.popup import StepPopup

# # Image utilities
# from utils.image_utils import (
#     annotate_screenshot,
#     smart_resize,
# )

# # Vision utilities (VLM message building)
# # from utils.vision_utils import (
# #     build_messages,
# #     encode_image_to_base64,
# #     image_to_base64,
# #     pil_to_base64,
# # )

# # Parsers
# from utils.parsers import (
#     extract_tool_calls,
#     extract_template_request,
#     extract_action,
# )

# # LLM wrappers
# # from utils.llm_wrapper import (
# #     LlmWrapper,
# #     MultimodalLlmWrapper,
# #     GUIOwlWrapper,
# #     ERROR_CALLING_LLM,
# # )

# # Helpers
# # from utils.helpers import (
# #     get_output_dir,
# #     sanitize_filename,
# #     format_step_text,
# #     process_markdown_task,
# # )


# # OpenCV availability flag
# try:
#     import cv2
#     CV2_AVAILABLE = True
# except ImportError:
#     CV2_AVAILABLE = False
#     print("[WARN] OpenCV (cv2) not installed. Template matching disabled.")
#     print("       Install with: pip install opencv-python")

__all__ = [
    # Classes
    "ComputerTools",
    "StepPopup",
    "TemplateMatcher",
    "LlmWrapper",
    "MultimodalLlmWrapper",
    "GUIOwlWrapper",

    # Image utils
    "annotate_screenshot",
    "smart_resize",

    # Vision utils
    "build_messages",
    "encode_image_to_base64",
    "image_to_base64",
    "pil_to_base64",

    # Parsers
    "extract_tool_calls",
    "extract_template_request",
    "extract_action",

    # Helpers
    "get_output_dir",
    "sanitize_filename",
    "format_step_text",
    "process_markdown_task",

    # Flags
    "CV2_AVAILABLE",
    "ERROR_CALLING_LLM",
]
