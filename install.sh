#!/usr/bin/env bash
# One-step installer for HyperX Haste 2 Pro (Linux driver + settings app).
#
#   ./install.sh              install
#   ./install.sh --uninstall  remove everything this script installed
#   ./install.sh --dry-run    show what would happen without changing anything
#   ./install.sh --yes        don't ask for confirmation
set -uo pipefail

APP=hyperx-haste2
APP_NAME="HyperX Haste 2 Pro"
RULE=70-hyperx-pulsefire-haste2-pro.rules
PREFIX=/usr/local                       # non-Arch installs
LIB_DIR=$PREFIX/lib/$APP
SRC=$(cd "$(dirname "$0")" && pwd)
LOG=$(mktemp -t "$APP-install.XXXXXX.log")

DRY_RUN=0 ASSUME_YES=0 MODE=install
for arg in "$@"; do
  case $arg in
    --uninstall) MODE=uninstall ;;
    --dry-run) DRY_RUN=1 ;;
    -y|--yes) ASSUME_YES=1 ;;
    -h|--help) sed -n '2,8p' "$0" | sed 's/^# \{0,1\}//'; exit 0 ;;
    *) echo "unknown option: $arg (try --help)"; exit 2 ;;
  esac
done

# --- output ---------------------------------------------------------------
if [[ -t 1 ]]; then
  B=$'\e[1m' DIM=$'\e[2m' RED=$'\e[31m' GRN=$'\e[32m' YEL=$'\e[33m' CYN=$'\e[36m' R=$'\e[0m'
else
  B='' DIM='' RED='' GRN='' YEL='' CYN='' R=''
fi
STEP=0 TOTAL=0

banner() {
  printf '\n%s  %s%s\n' "$B$CYN" "$1" "$R"
  printf '%s  %s%s\n\n' "$DIM" "$2" "$R"
}
step() { STEP=$((STEP + 1)); printf '\n%s[%d/%d] %s%s\n' "$B" "$STEP" "$TOTAL" "$1" "$R"; }
info() { printf '      %s\n' "$*"; }
ok()   { printf '      %s✔%s %s\n' "$GRN" "$R" "$*"; }
warn() { printf '      %s!%s %s\n' "$YEL" "$R" "$*"; }
fail() {
  printf '      %s✖ %s%s\n' "$RED" "$*" "$R"
  if [[ -s $LOG ]]; then
    printf '\n%sLast lines of the log (%s):%s\n' "$DIM" "$LOG" "$R"
    tail -n 15 "$LOG" | sed 's/^/      /'
  fi
  printf '\n%sInstallation stopped. Nothing after this step was changed.%s\n' "$RED" "$R"
  exit 1
}

# run "description" cmd...  : runs with a spinner, output to the log
run() {
  local msg=$1; shift
  if ((DRY_RUN)); then
    printf '      %s→%s %s %s(would run: %s)%s\n' "$CYN" "$R" "$msg" "$DIM" "$*" "$R"
    return 0
  fi
  echo "### $msg: $*" >>"$LOG"
  "$@" >>"$LOG" 2>&1 &
  local pid=$! frames='⠋⠙⠹⠸⠼⠴⠦⠧⠇⠏' i=0
  if [[ -t 1 ]]; then
    while kill -0 "$pid" 2>/dev/null; do
      printf '\r      %s%s%s %s' "$CYN" "${frames:i++%${#frames}:1}" "$R" "$msg"
      sleep 0.1
    done
  fi
  local status=0
  wait "$pid" || status=$?
  [[ -t 1 ]] && printf '\r\e[K'
  ((status == 0)) && ok "$msg" || fail "Failed: $msg"
}

confirm() {
  ((ASSUME_YES || DRY_RUN)) && return 0
  read -r -p "      $1 [Y/n] " reply
  [[ -z $reply || $reply =~ ^[Yy] ]]
}

# --- system detection -----------------------------------------------------
detect_distro() {
  local id='' like=''
  if [[ -r /etc/os-release ]]; then
    id=$(. /etc/os-release; echo "${ID:-}")
    like=$(. /etc/os-release; echo "${ID_LIKE:-}")
    PRETTY=$(. /etc/os-release; echo "${PRETTY_NAME:-$id}")
  fi
  case " $id $like " in
    *" arch "*) FAMILY=arch ;;
    *" debian "*|*" ubuntu "*) FAMILY=debian ;;
    *" fedora "*|*" rhel "*) FAMILY=fedora ;;
    *" suse "*|*" opensuse "*) FAMILY=suse ;;
    *) FAMILY=unknown ;;
  esac
}

sudo_keepalive() {
  ((DRY_RUN)) && return 0
  info "Some steps need administrator rights. You may be asked for your password once."
  sudo -v || fail "could not get administrator rights"
  ( while kill -0 $$ 2>/dev/null; do sudo -n -v 2>/dev/null; sleep 50; done ) &
  KEEPALIVE=$!
  trap 'kill "$KEEPALIVE" 2>/dev/null' EXIT
}

mouse_nodes() {
  local d
  for d in /sys/class/hidraw/hidraw*; do
    grep -qiE 'HID_ID=0003:000003F0:00000(CBD|ABD)' "$d/device/uevent" 2>/dev/null && echo "/dev/${d##*/}"
  done
}

# --- install steps --------------------------------------------------------
install_deps() {
  case $FAMILY in
    arch)
      local need=()
      for p in python python-pyqt6 python-evdev base-devel python-build python-installer python-setuptools python-wheel; do
        pacman -Qq "$p" &>/dev/null || need+=("$p")
      done
      if ((${#need[@]})); then
        info "Installing: ${need[*]}"
        run "Installing packages with pacman" sudo pacman -S --needed --noconfirm "${need[@]}"
      else
        ok "All required packages are already installed"
      fi ;;
    debian) run "Installing python3, PyQt6 and evdev with apt" \
              sudo sh -c 'apt-get update && apt-get install -y python3 python3-pyqt6 python3-evdev' ;;
    fedora) run "Installing python3, PyQt6 and evdev with dnf" sudo dnf install -y python3 python3-pyqt6 python3-evdev ;;
    suse)   run "Installing python3, PyQt6 and evdev with zypper" \
              sudo zypper --non-interactive install python3 python3-PyQt6 python3-evdev ;;
    *)
      if python3 -c 'import PyQt6' &>/dev/null; then
        ok "python3 and PyQt6 found"
      else
        fail "Unknown distribution. Install python3 and PyQt6 with your package manager, then run this again."
      fi ;;
  esac
}

install_app_arch() {
  local build
  build=$(mktemp -d -t "$APP-build.XXXXXX")
  run "Bundling the source" "$SRC/packaging/arch/make-tarball.sh"
  ((DRY_RUN)) || cp "$SRC"/packaging/arch/{PKGBUILD,"$APP"-*.tar.gz} "$build/"
  run "Building the package (makepkg)" sh -c "cd '$build' && makepkg -f --noconfirm"
  if ((DRY_RUN)); then
    run "Installing the package with pacman" sudo pacman -U --noconfirm "$build/$APP-*.pkg.tar.zst"
  else
    run "Installing the package with pacman" sh -c "sudo pacman -U --noconfirm '$build'/$APP-*.pkg.tar.zst"
  fi
  rm -rf "$build"
  info "Installed as a system package, so ${B}sudo pacman -R $APP${R} also removes it."
}

install_app_generic() {
  local wrapper
  wrapper=$(mktemp)
  cat >"$wrapper" <<EOF
#!/bin/sh
PYTHONPATH="$LIB_DIR\${PYTHONPATH:+:\$PYTHONPATH}" exec python3 -m hyperx_haste2 "\$@"
EOF
  run "Copying the app to $LIB_DIR" sudo install -Dm644 -t "$LIB_DIR/hyperx_haste2" \
      "$SRC"/hyperx_haste2/{__init__,__main__,cli,desktop,gui,protocol,remap}.py
  run "Adding the '$APP' command" sudo install -Dm755 "$wrapper" "$PREFIX/bin/$APP"
  run "Adding the app-menu entry" sudo install -Dm644 -t "$PREFIX/share/applications" "$SRC/packaging/$APP.desktop"
  run "Adding the icon" sudo install -Dm644 -t "$PREFIX/share/icons/hicolor/scalable/apps" "$SRC/packaging/$APP.svg"
  rm -f "$wrapper"
}

install_rule_generic() {
  run "Installing the udev rule" sudo install -Dm644 "$SRC/udev/$RULE" "/etc/udev/rules.d/$RULE"
}

reload_udev() {
  run "Reloading udev rules" sudo udevadm control --reload
  run "Applying the rule to connected devices" sudo udevadm trigger --subsystem-match=hidraw --subsystem-match=input --action=change
  run "Loading the uinput module (button bindings)" sudo modprobe uinput
  run "Applying the rule to /dev/uinput" sudo udevadm trigger --subsystem-match=misc --sysname-match=uinput --action=change
  run "Waiting for udev to finish" sudo udevadm settle
}

verify() {
  local nodes
  nodes=$(mouse_nodes)
  if [[ -z $nodes ]]; then
    warn "Mouse not detected right now. Plug in the dongle (or cable) and open the app."
    return
  fi
  ok "Mouse found: $(echo "$nodes" | tr '\n' ' ')"
  if ((DRY_RUN)); then
    info "(dry run: skipping the access check)"
    return
  fi
  local n denied=0
  for n in $nodes; do [[ -r $n && -w $n ]] || denied=1; done
  if ((denied)); then
    warn "No access to the mouse yet. Unplug and replug the dongle, then open the app."
    return
  fi
  ok "You have access to the mouse (no root needed)"
  local out
  if out=$("$APP" status 2>&1); then
    ok "Talking to the mouse works:"
    echo "$out" | grep -E '^(Battery|Polling)' | sed 's/^/          /'
  else
    warn "Mouse found but did not answer (it may be asleep, move it): ${out##*error: }"
  fi
}

do_install() {
  TOTAL=5
  banner "$APP_NAME installer" "Linux driver and settings app for the HyperX Pulsefire Haste 2 Pro 4K"
  ((DRY_RUN)) && printf '      %sDry run: nothing will be changed.%s\n' "$YEL" "$R"

  step "Checking your system"
  [[ $EUID -eq 0 ]] && fail "Run this as your normal user, not root (it will ask for sudo when needed)."
  detect_distro
  ok "Detected: ${PRETTY:-unknown} (${FAMILY})"
  command -v sudo >/dev/null || fail "sudo is required"
  ok "sudo is available"
  info "This will install:"
  info "  • the '$APP' command and an app-menu entry \"$APP_NAME\""
  info "  • a udev rule so your user can talk to the mouse without root"
  confirm "Continue?" || { echo "      Cancelled."; exit 0; }
  sudo_keepalive

  step "Installing dependencies"
  install_deps

  step "Installing $APP_NAME"
  if [[ $FAMILY == arch ]]; then install_app_arch; else install_app_generic; fi

  step "Setting up mouse access (udev)"
  if [[ $FAMILY == arch ]]; then
    ok "udev rule installed by the package"
    # A hand-copied rule from an earlier manual setup would duplicate the packaged one.
    [[ -e /etc/udev/rules.d/$RULE ]] && run "Removing the old hand-installed copy of the rule" sudo rm -f "/etc/udev/rules.d/$RULE"
  else
    install_rule_generic
  fi
  reload_udev

  step "Checking the mouse"
  verify

  printf '\n%s%s✔ Done!%s\n' "$B" "$GRN" "$R"
  info "Open ${B}$APP_NAME${R} from your app menu, or run ${B}$APP${R}."
  info "Command line: ${B}$APP status${R}, ${B}$APP set --dpi 400 800 1600 3200${R}"
  info "To remove it later: ${B}$SRC/install.sh --uninstall${R}"
  ((DRY_RUN)) || info "${DIM}Install log: $LOG${R}"
}

do_uninstall() {
  TOTAL=2
  banner "$APP_NAME uninstaller" "Removes the app, its menu entry and the udev rule"
  detect_distro
  confirm "Remove $APP_NAME?" || { echo "      Cancelled."; exit 0; }
  sudo_keepalive

  step "Removing $APP_NAME"
  if pacman -Qq "$APP" &>/dev/null; then
    run "Removing the package with pacman" sudo pacman -R --noconfirm "$APP"
  fi
  local f files=("$PREFIX/bin/$APP" "$PREFIX/share/applications/$APP.desktop"
                 "$PREFIX/share/icons/hicolor/scalable/apps/$APP.svg" "/etc/udev/rules.d/$RULE")
  for f in "${files[@]}"; do
    [[ -e $f ]] && run "Removing $f" sudo rm -f "$f"
  done
  [[ -d $LIB_DIR ]] && run "Removing $LIB_DIR" sudo rm -rf "$LIB_DIR"

  step "Reloading udev"
  reload_udev
  printf '\n%s%s✔ Removed.%s\n' "$B" "$GRN" "$R"
  info "Your mouse keeps whatever settings you saved to it."
}

((DRY_RUN)) && rm -f "$LOG"
if [[ $MODE == uninstall ]]; then do_uninstall; else do_install; fi
