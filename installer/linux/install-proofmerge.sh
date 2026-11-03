#!/bin/sh
# Install ProofMerge for the current Linux user. Run: sh install-proofmerge.sh
# Running it again updates ProofMerge.
#
# What it does, in order:
#   1. finds Python 3.11 or newer with venv support
#   2. finds Git
#   3. looks for the GitHub CLI, which lets the app sign in to GitHub (optional)
#   4. installs ProofMerge into ~/.local/share/proofmerge/venv (no sudo, PATH untouched)
#   5. checks that Docker, which runs the tests in a sandbox, is reachable
#   6. adds ProofMerge and Uninstall ProofMerge to the applications menu
#   7. opens ProofMerge in the browser (skipped when PROOFMERGE_NO_OPEN=1)
# It never uses sudo or installs system packages: for a missing one it prints the command.

set -eu

REPO=https://github.com/imthi16/proofmerge
REF=${PROOFMERGE_REF:-main}
DATA=${XDG_DATA_HOME:-$HOME/.local/share}
INSTALL_DIR=$DATA/proofmerge
VENV=$INSTALL_DIR/venv
APPS=$DATA/applications

step() { printf '\n  \033[36m%s\033[0m\n' "$1"; }
say() { printf '    %s\n' "$1"; }
ok() { printf '    \033[32m%s\033[0m\n' "$1"; }
fail() { printf '\n    \033[31m%s\033[0m\n' "$1" >&2; exit 1; }
has() { command -v "$1" >/dev/null 2>&1; }

# The command that installs system packages on this distribution, for the user to run.
pkg_hint() {
    if has apt-get; then echo "sudo apt-get install $1"
    elif has dnf; then echo "sudo dnf install $2"
    elif has pacman; then echo "sudo pacman -S $3"
    elif has zypper; then echo "sudo zypper install $2"
    else echo "your package manager (package: $2)"
    fi
}

# The ref goes into a pip requirement; allow only branch, tag and commit characters.
case $REF in
    *..*) fail "PROOFMERGE_REF '$REF' is not a branch, tag or commit name." ;;
esac
if ! printf '%s' "$REF" | grep -Eq '^[A-Za-z0-9][A-Za-z0-9._/-]{0,199}$'; then
    fail "PROOFMERGE_REF '$REF' is not a branch, tag or commit name."
fi
# Desktop entry Exec lines give these characters a meaning; refuse rather than escape them.
case $VENV in
    *[\"\`\$\\]*) fail "The install folder $VENV has a quote, \$ or backslash in it." ;;
esac

printf '\n  ProofMerge installer\n  Checks a code change before you merge it, with evidence.\n'

step 'Step 1 of 7: Python'
PYTHON=
for candidate in python3.13 python3.12 python3.11 python3; do
    if has "$candidate" && "$candidate" -c 'import sys; sys.exit(sys.version_info < (3, 11))' 2>/dev/null; then
        PYTHON=$candidate
        break
    fi
done
[ -n "$PYTHON" ] || fail "ProofMerge needs Python 3.11 or newer. Install it with: $(pkg_hint python3 python3 python), then run this again."
# Debian and Ubuntu ship venv separately; ensurepip is what a venv without pip lacks.
"$PYTHON" -c 'import ensurepip, venv' 2>/dev/null \
    || fail "Python's venv module is missing. Install it with: $(pkg_hint python3-venv python3 python), then run this again."
ok "Found $("$PYTHON" --version)"

step 'Step 2 of 7: Git'
has git || fail "ProofMerge reads your branches with Git. Install it with: $(pkg_hint git git git), then run this again."
ok "Found $(git --version)"

step 'Step 3 of 7: GitHub sign-in'
if has gh; then
    ok "Found $(gh --version | head -n 1)"
else
    say 'Skipped: the GitHub CLI is not installed. You can still check changes in folders on this computer.'
    say 'To sign in to GitHub from the app later, install it: https://github.com/cli/cli#installation'
fi

step 'Step 4 of 7: ProofMerge'
mkdir -p "$INSTALL_DIR"
if [ ! -x "$VENV/bin/python" ]; then
    say "Making a private Python environment in $VENV"
    "$PYTHON" -m venv "$VENV" || fail 'Could not create the Python environment.'
fi
say "Downloading ProofMerge ($REF) from $REPO. This takes a minute."
# Same version number, new code: pip keeps an installed copy unless forced. Forcing only this
# package replaces the old copy after the new one has downloaded and built, so a failed
# download leaves the working install in place. The second call adds any new dependencies.
"$VENV/bin/python" -m pip install --quiet --disable-pip-version-check --force-reinstall --no-deps "proofmerge @ git+$REPO@$REF" \
    && "$VENV/bin/python" -m pip install --quiet --disable-pip-version-check "proofmerge @ git+$REPO@$REF" \
    || fail 'ProofMerge could not be installed. Check the internet connection and try again.'
[ -x "$VENV/bin/proofmerge-app" ] || fail "The ProofMerge launcher is missing: $VENV/bin/proofmerge-app"
ok "Installed $("$VENV/bin/proofmerge" version)"

step 'Step 5 of 7: Docker'
if ! has docker; then
    say 'ProofMerge runs the tests inside Docker, so the change never touches your files.'
    say 'Docker is not installed. Install Docker Engine: https://docs.docker.com/engine/install/'
    say 'The Setup page in ProofMerge checks it again when you are ready.'
elif docker info >/dev/null 2>&1; then
    ok 'Found Docker, and it is running.'
else
    say 'Docker is installed but not reachable. Start it, and if it says permission denied, run:'
    say '  sudo usermod -aG docker "$USER"    then log out and back in.'
fi

step 'Step 6 of 7: Applications menu'
mkdir -p "$APPS"
cat > "$INSTALL_DIR/uninstall.sh" <<EOF
#!/bin/sh
# Remove ProofMerge. Your checks in ~/ProofMerge stay where they are.
rm -f "$APPS/proofmerge.desktop" "$APPS/proofmerge-uninstall.desktop"
rm -rf "$INSTALL_DIR"
echo 'ProofMerge removed. Your checks in ~/ProofMerge stay where they are.'
EOF
chmod +x "$INSTALL_DIR/uninstall.sh"
cat > "$APPS/proofmerge.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=ProofMerge
Comment=Check a change before you merge it
Exec="$VENV/bin/proofmerge-app"
Icon=applications-development
Terminal=false
Categories=Development;
EOF
cat > "$APPS/proofmerge-uninstall.desktop" <<EOF
[Desktop Entry]
Type=Application
Name=Uninstall ProofMerge
Comment=Remove ProofMerge; your checks stay
Exec="$INSTALL_DIR/uninstall.sh"
Icon=edit-delete
Terminal=true
Categories=Development;
EOF
has update-desktop-database && update-desktop-database "$APPS" >/dev/null 2>&1 || true
ok 'Added ProofMerge and Uninstall ProofMerge to the applications menu.'

step 'Step 7 of 7: Open ProofMerge'
if [ "${PROOFMERGE_NO_OPEN:-}" = 1 ]; then
    say 'Skipped (PROOFMERGE_NO_OPEN=1).'
else
    # Detached, so closing this terminal does not stop it; press Quit in the app to stop it.
    nohup "$VENV/bin/proofmerge-app" >"$INSTALL_DIR/app.log" 2>&1 &
    ok 'ProofMerge is opening in your browser. Press Quit in the app to stop it.'
fi
say 'Next time, open ProofMerge from the applications menu.'
