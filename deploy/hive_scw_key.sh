# Resolves the Scaleway key that agent VMs are allowed to hold. Sourced, not run.
#
# Never `scw config get secret-key`: that is a personal key and inherits its
# owner's group policies — for an org Administrator, AllProductsFullAccess
# org-wide. This value ends up in /etc/hive/env, the EnvironmentFile for
# hive-runner, so it becomes an environment variable in every agent process on
# the box. It must be an IAM *application* key scoped to the hive project.
#
# Sets HIVE_SCW_SECRET_KEY, or exits non-zero.

HIVE_SCW_SECRET_KEY=${HIVE_SCW_SECRET_KEY:-$(awk '/^## hive-vm/,/^## [^h]/ {if ($1 == "Secret" && $2 == "Key:") print $3}' ~/secrets/scaleway.md 2>/dev/null)}
[ -n "$HIVE_SCW_SECRET_KEY" ] || {
  echo "no hive-vm key: set HIVE_SCW_SECRET_KEY, or add a '## hive-vm' section to ~/secrets/scaleway.md" >&2
  exit 1
}

# Least privilege is a property, so assert it instead of trusting where the key
# came from: anything that can read IAM can grant itself more. 403 is the pass.
_iam_status=$(curl -s -o /dev/null -w '%{http_code}' -H "X-Auth-Token: $HIVE_SCW_SECRET_KEY" \
  "https://api.scaleway.com/iam/v1alpha1/policies?organization_id=$(scw config get default-organization-id)")
[ "$_iam_status" = "403" ] || {
  echo "refusing to push this key: it reads IAM (HTTP $_iam_status, want 403) — too privileged for an agent VM" >&2
  exit 1
}
unset _iam_status
