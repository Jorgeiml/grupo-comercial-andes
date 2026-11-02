# Leaves the demo environment clean before a rehearsal or the live presentation:
# ERP back to seed data, and a fresh agent database (proposals, approvals, audit, costs, conversations).
# The policy index (Chroma) is kept, so no embeddings need to be paid for again.
# The audit trail is never edited or deleted row by row: the whole demo database is replaced.

docker compose up -d mock-erp agent-api | Out-Null
docker compose exec -T mock-erp python -c "import os, urllib.request as u; u.urlopen(u.Request('http://localhost:8001/admin/reset', method='POST', headers={'Authorization': 'Bearer ' + os.environ['ERP_ADMIN_TOKEN']}))"
docker compose exec -T agent-api sh -c "rm -f /data/agent.db /data/checkpoints.db"
docker compose restart agent-api | Out-Null
docker compose up -d ui | Out-Null
Write-Output "Demo reset: ERP on seed data, empty proposal queue and audit trail. UI: http://localhost:8501"
