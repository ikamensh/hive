#!/bin/bash
# Run locally: creates the hive VM (chief + runner) in project hive-ikamen.
# Re-runnable: updates the startup script and restarts the VM if it exists.
set -euo pipefail
PROJECT=hive-ikamen
ZONE=europe-west1-b
VM=hive-vm
ACCOUNT=ikamenshchikov@gmail.com
SA="$(gcloud projects describe $PROJECT --format='value(projectNumber)' --account=$ACCOUNT)-compute@developer.gserviceaccount.com"

# Default compute SA needs Firestore, GCS, and Secret Manager access.
for role in roles/datastore.user roles/storage.objectAdmin roles/secretmanager.secretAccessor; do
  gcloud projects add-iam-policy-binding $PROJECT --member="serviceAccount:$SA" \
    --role=$role --account=$ACCOUNT --condition=None --quiet >/dev/null
done

if gcloud compute instances describe $VM --zone=$ZONE --project=$PROJECT --account=$ACCOUNT >/dev/null 2>&1; then
  gcloud compute instances add-metadata $VM --zone=$ZONE --project=$PROJECT --account=$ACCOUNT \
    --metadata-from-file=startup-script=deploy/vm_startup.sh
  gcloud compute instances reset $VM --zone=$ZONE --project=$PROJECT --account=$ACCOUNT
else
  # e2-medium (4GB) fits the measured load (chief+runner idle ~1GB, agent CLIs
  # spike ~1GB mid-task; swap in vm_startup.sh is the backstop). The static IP
  # is pinned because the public hostnames (Caddyfile) encode 34.62.218.54.
  gcloud compute instances create $VM \
    --zone=$ZONE --project=$PROJECT --account=$ACCOUNT \
    --machine-type=e2-medium \
    --address=hive-vm-ip \
    --image-family=debian-12 --image-project=debian-cloud \
    --boot-disk-size=30GB --boot-disk-type=pd-balanced \
    --service-account="$SA" --scopes=cloud-platform \
    --metadata-from-file=startup-script=deploy/vm_startup.sh
fi
echo "VM ready. Web UI tunnel:  gcloud compute ssh $VM --zone=$ZONE --project=$PROJECT -- -L 8000:localhost:8000"
