# Sophia: Voice First AI Triage Assistant

Real-time AI medical triage using Amazon Nova Sonic 2 (speech-to-speech).

---

## Quick Start (AWS)

### 1. Deploy (Terraform + AgentCore)

```bash
chmod +x deployment/scripts/*.sh

./deployment/scripts/deploy.sh
```

### 2. Update frontend only (optional)

```bash
./deployment/scripts/deploy_frontend_only.sh
```

### 3. Cleanup

```bash
./deployment/scripts/destroy.sh
```