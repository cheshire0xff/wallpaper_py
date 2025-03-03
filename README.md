# wallpaper_py

wallpaper_py is a cross‑platform library for managing desktop wallpapers. The project defines a minimal base protocol for common wallpaper management functionality and provides platform‑specific implementations. On Windows, additional monitor details (such as unique monitor identifiers) are required by the Windows APIs. These extra details are encapsulated in Windows‑specific types located in the `windows` directory.

## Overview

The library is built around a base protocol defined in `desktop_protocol.py` that includes:

- **MonitorDescription:**  
  A minimal interface that describes a monitor (e.g., using a `Rectangle` for monitor bounds).

- **DesktopManager:**  
  An interface for retrieving monitor information and setting wallpapers, which all platform-specific implementations must follow.

Cross‑platform code depends only on these base protocols. Platform‑specific implementations extend the base protocols only where necessary.

### Windows‑Specific Extensions

On Windows, extra information is needed to properly interface with the desktop wallpaper API. To address this without "polluting" the cross‑platform interface:

- **WindowsMonitorDescription:**  
  Defined in `windows/desktop_manager.py`, this data class extends the base `MonitorDescription` with extra attributes such as `id` (a unique identifier) and `index` (the monitor’s position in the system).

- **WindowsDesktopManager:**  
  The Windows implementation of `DesktopManager` (found in the `windows` directory) uses these extended types along with COM (via comtypes) to interact with Windows APIs. It includes runtime type checks to ensure that the provided monitor description contains the Windows‑specific fields.

This separation keeps the cross‑platform core clean while isolating Windows‑only behavior to its dedicated module.

## Project Structure

- **`desktop_protocol.py`**  
  Defines the minimal, cross‑platform protocols for monitor descriptions and desktop management.

- **`windows/desktop_manager.py`**  
  Contains the Windows‑specific implementation. It provides:
  - `WindowsMonitorDescription` – an extension of the base monitor description.
  - `WindowsMonitor` – the monitor type used by Windows.
  - Windows-specific methods for querying monitor details and setting wallpapers.

- **`windows/desktop_wallpaper.py`**  
  Wraps Windows COM interfaces to interact with the system’s desktop wallpaper API.

- **`image.py`**  
  Contains image processing functions (for modes such as FILL, FIT, and STRETCH) that resize and crop images to match target monitor resolutions.

- **`cli_parsers.py` and `changer.py`**  
  Provide a command‑line interface (CLI) for listing monitors and setting wallpapers. The CLI depends only on the base protocols, while the underlying implementation is selected based on the platform.

- **(Optional) Factory Module:**  
  A module (or conditional import in `desktop_manager.py`) may choose the appropriate implementation (Windows or stub/alternative for other platforms) at runtime.

## Usage

### Command‑Line Interface (CLI)

- **List Monitors:**

  ```bash
  python -m wallpaper_py changer list
  ```

  This command displays all connected monitors along with their positions, sizes, and current wallpaper paths.

- **Set Wallpaper:**

  ```bash
  python -m wallpaper_py changer set /path/to/image.jpg --monitor 0 --mode FIT
  ```

  This sets the wallpaper on monitor 0 using the FIT mode. The image is processed to match the monitor's resolution.

### Code Integration

- **Cross‑Platform Code:**  
  Code that is intended to be cross‑platform should depend only on the base protocols defined in `desktop_protocol.py`. It will not assume the presence of Windows‑specific fields.

- **Windows‑Specific Code:**  
  When running on Windows, you can safely use the extended types (such as `WindowsMonitorDescription`) to access extra attributes like `id` or `index`. If needed, a runtime type check (or an explicit cast) can be performed to ensure that a monitor description meets the Windows‑specific contract.

## Design Considerations

### Platform Agnosticism

- **Common Core:**  
  The base protocols define what is essential across all platforms (e.g., monitor geometry and basic wallpaper management).

- **Isolation of Windows‑Specific Requirements:**  
  By placing Windows‑specific types and implementations in a dedicated module (the `windows` directory), the design keeps platform‑dependent details isolated. This makes the core API truly cross‑platform and makes it easier to add or update platform‑specific functionality in the future.

### Liskov Substitution Principle (LSP)

- **Minimal Base Contract:**  
  The base protocol guarantees only the attributes common to all platforms. Windows‑specific details are not part of the base contract.

- **Extended Windows Types:**  
  Windows‑specific implementations (like `WindowsMonitorDescription`) include extra fields needed only on Windows. Windows‑only code can safely cast to these richer types. Cross‑platform code remains unaffected, adhering to LSP.

## Contributing

Contributions are welcome! Please refer to `CONTRIBUTING.md` for guidelines. If adding platform‑specific features, document them clearly and ensure that the cross‑platform core remains independent.

## License

This project is licensed under the [MIT License](LICENSE).