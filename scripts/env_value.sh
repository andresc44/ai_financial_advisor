# Sourced by scripts/cron_job.sh and scripts/backup_to_drive.sh. Claude-authored (2026-10-16).
#
# env_value KEY prints KEY's value from .env (in the current directory) without sourcing the file, so nothing else in
# .env can run as shell code. Follows python-dotenv: the last KEY= line wins, "export " is allowed, a quoted value
# ends at its closing quote, and an unquoted value ends at a " # comment". Prints nothing when KEY isn't set.
env_value() {
  [ -f .env ] || return 0
  local line value
  line="$(grep -E "^[[:space:]]*(export[[:space:]]+)?$1[[:space:]]*=" .env | tail -n 1 | tr -d '\r')" || true
  [ -n "$line" ] || return 0
  value="${line#*=}"
  value="${value#"${value%%[![:space:]]*}"}"  # Leading whitespace
  case "$value" in
    \"*) value="${value#\"}"; value="${value%%\"*}" ;;
    \'*) value="${value#\'}"; value="${value%%\'*}" ;;
    \#*) value="" ;;  # Only a comment, e.g. "KEY=  # optional"
    *) value="$(printf '%s' "$value" | sed -E -e 's/[[:space:]]+#.*$//' -e 's/[[:space:]]+$//')" ;;
  esac
  printf '%s' "$value"
}
