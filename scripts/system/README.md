# scripts/system: steps that need sudo

Claude Code cannot type a sudo password, so anything that needs root lives here.
Read a script before you run it. Each one is safe to run twice.

| Script | When | Run |
|---|---|---|
| `01_base_packages.sh` | Always | `sudo bash scripts/system/01_base_packages.sh` |
| `02_cuda_toolkit_wsl.sh` | Only if `nvidia-smi` shows an NVIDIA GPU | `sudo bash scripts/system/02_cuda_toolkit_wsl.sh` |

`02_cuda_toolkit_wsl.sh` installs the CUDA toolkit from NVIDIA's WSL-Ubuntu repository,
choosing the newest `cuda-toolkit-X-Y` that your Windows driver supports. It never installs
a Linux GPU driver. If it finds Ubuntu's `nvidia-cuda-toolkit` or Linux driver libraries
(`libnvidia-compute-*`), it removes them, because on WSL2 they put a second `libcuda.so` next
to the one Windows provides.
