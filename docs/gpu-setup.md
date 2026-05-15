# GPU Setup for airig (4x RTX 3090)

## Problem
After installing NVIDIA drivers, Ollama defaults to CPU because
it starts before GPU libraries are fully initialized.

## Fix Applied

### 1. Install driver
```bash
sudo apt install -y nvidia-driver-595-open
sudo reboot
```

### 2. Fix library discovery
```bash
sudo tee /etc/ld.so.conf.d/cuda.conf << 'CONF'
/usr/lib/x86_64-linux-gnu
/usr/lib/x86_64-linux-gnu/nvidia
/lib/x86_64-linux-gnu
CONF
sudo ldconfig
```

### 3. Ollama systemd override
```bash
sudo tee /etc/systemd/system/ollama.service.d/override.conf << 'CONF'
[Unit]
After=network-online.target nvidia-persistenced.service
Wants=nvidia-persistenced.service

[Service]
Environment="OLLAMA_HOST=0.0.0.0"
Environment="OLLAMA_MODELS=/mnt/qnap/models/ollama"
Environment="CUDA_VISIBLE_DEVICES=0,1,2,3"
Environment="OLLAMA_GPU_DRIVER=cuda"
ExecStartPre=/bin/sh -c 'until nvidia-smi > /dev/null 2>&1; do sleep 1; done'
CONF
sudo systemctl daemon-reload
sudo systemctl restart ollama
```

### Verification
```bash
ollama run qwen3:30b "READY" &
sleep 8
ollama ps  # Should show 100% GPU
```

## Result
- Both qwen3:30b and bge-m3 load on GPU after reboot
- Pipeline cycle: 14.7s (was 17+ minutes on CPU)
- Per-article scoring: ~0.35s (was ~20s on CPU)
