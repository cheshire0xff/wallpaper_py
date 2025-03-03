# Architecture Overview

This project provides a cross‑platform API for managing desktop wallpapers. The design is built around a minimal base protocol that defines the common properties and behaviors required for wallpaper management, with platform‑specific implementations extending that protocol as needed.

## Base Protocol

The base protocol, defined in `desktop_protocol.py`, establishes the minimal contract that applies to all platforms. It includes:

- **MonitorDescription Protocol:**  
  Specifies the common attributes (such as a `Rectangle` describing monitor geometry) required for a monitor. This is the only guarantee that cross‑platform code can rely on.

- **DesktopManager Protocol:**  
  Declares the methods for listing monitors (`get_monitors()`), setting wallpapers (`set_wallpaper()`), and managing wallpaper modes (e.g., `get_supported_modes()`, `set_global_mode()`, etc.).

Cross‑platform code is written against these protocols so that it remains agnostic to platform-specific details.

## Windows‑Specific Extensions

On Windows, additional information is required to interface properly with the Windows desktop wallpaper API (via COM). To support these extra needs:

- **WindowsMonitorDescription:**  
  Defined in your Windows‑specific module (inside the `windows` directory), this data class extends the base protocol by including extra attributes such as:
  - `id`: A unique identifier for the monitor.
  - `index`: The monitor's index within the system.
  
  This extension allows your Windows implementation to use extra details required by the Windows API, while cross‑platform code continues to depend only on the base protocol.

- **WindowsMonitor and WindowsDesktopManager:**  
  Similarly, the Windows‑specific monitor (here named `WindowsMonitor`) and desktop manager (currently named `DesktopManager` within the Windows folder) implement the base protocol. Their implementations use runtime type checking or casting to ensure that the richer Windows‑specific information is available. For example, the `set_wallpaper` method checks that the provided monitor description is a `WindowsMonitorDescription` before accessing its `id` field.

## Factory and Conditional Imports

A factory module (e.g. `desktop_manager_factory.py`) selects the proper implementation at runtime based on the current platform. On Windows, it imports the Windows‑specific implementation; on other platforms, it might import a stub or alternative version. This keeps the cross‑platform interface clean and ensures that only platform‑specific code uses the extra details.

## Additional Components

- **Image Processing:**  
  Implemented in `image.py`, the image processing functions (such as for the modes FILL, FIT, and STRETCH) are split into separate helper functions. This modular design simplifies testing and maintenance.

- **Command‑Line Interface (CLI):**  
  The CLI (provided via `cli_parsers.py` and `changer.py`) enables users to list monitors and set wallpapers. It relies only on the base protocol, while the underlying implementation (conditionally imported) handles platform‑specific details.

## Liskov Substitution Principle (LSP) Considerations

The design is structured to preserve cross‑platform substitutability:
- **Minimal Base Contract:**  
  The base protocol defines only the common functionality. Cross‑platform code is written against this minimal contract.
- **Windows‑Specific Details Isolated:**  
  Windows‑specific attributes (like monitor `id`) are provided only in the Windows‑specific extension (`WindowsMonitorDescription`). Windows‑only code can safely cast to this richer type because it is only used in the Windows context.
- **Clear Boundaries:**  
  By separating the base protocol from platform‑specific extensions, the design ensures that if a non‑Windows implementation is needed in the future, it only needs to conform to the base protocol without being burdened by Windows‑specific requirements.

## Summary

- **Cross‑Platform Core:**  
  The base protocol (in `desktop_protocol.py`) defines what is common for all platforms.
- **Windows‑Specific Implementation:**  
  Windows-specific details (like monitor `id` and `index`) are encapsulated in `WindowsMonitorDescription` and the corresponding Windows desktop manager (located in the `windows` directory). This isolates platform‑specific behavior.
- **Conditional Implementation:**  
  A factory mechanism or conditional import ensures that cross‑platform code uses the appropriate implementation at runtime.
- **Modular and Extensible:**  
  Image processing and CLI functionality are implemented in separate modules, making the design modular, testable, and easy to extend for other platforms.

This architecture ensures that cross‑platform code remains clean and independent of platform-specific extensions, while allowing the Windows implementation to leverage extra information required for its APIs.