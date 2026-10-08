# Qwen Image 2.1 protocol bridge

Standalone service-side wrapper for an existing stable-diffusion.cpp deployment.
Requires Python 3.10+ and Pillow; no Core or CUDA dependency at runtime.

Deployment and limits: [中文使用指南](../../docs/guides/qwen-image-service.md).
Real verification: [验收记录](../../docs/reports/qwen-generic-real-20260929.md).

Run protocol regression including the actual Core client from the repository root:

```bash
PYTHONPATH=src python -m unittest discover -s examples/qwen_service_bridge -p 'test_*.py'
```

Do not regenerate a manifest or clear the database to retry an unknown job.
The upstream API has no submission-key recovery: interrupted submissions remain
blocked until an administrator reconciles them. Original responses and PNGs stay
in the service data directory; published RGB uses explicit alpha-over-white.
