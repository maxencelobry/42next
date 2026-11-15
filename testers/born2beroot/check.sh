#!/bin/bash
# 42next - Born2beRoot checker (subject v1.3)
#
# Run it INSIDE the virtual machine, as root:
#     sudo bash check.sh [login] [path/to/monitoring.sh]
#
# login defaults to $SUDO_USER; monitoring.sh is found from the crontabs.
#
# It changes no configuration: it reads files and runs query commands
# (lsblk, sshd -T, ufw status, visudo -c, ...), and never edits, installs,
# enables or restarts anything. It also runs your monitoring.sh once, as
# root, with wall replaced by a print to stdout: a `wall` function, a `wall`
# (and a pass-through `sudo`) first in PATH, and /usr/bin/wall or /bin/wall
# in the script text rewritten to `wall`. Whatever else your script does
# when it runs, it does here too.
#
# Exit code: 0 no KO, 1 at least one KO.

export LC_ALL=C
PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:$PATH

# Latest stable releases known when this checker was written (2026-11).

# Values taken from the subject (v1.3).
SSH_PORT=4242
PASS_MAX=30
PASS_MIN=2
PASS_WARN=7
PW_MINLEN=10
PW_MAXREPEAT=3
PW_DIFOK=7
SUDO_TRIES=3
SUDO_LOGDIR=/var/log/sudo/
SECURE_PATH=/usr/local/sbin:/usr/local/bin:/usr/sbin:/usr/bin:/sbin:/bin:/snap/bin
CRON_EVERY=10

if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
	C_G=$'\033[38;5;78m'; C_R=$'\033[38;5;203m'; C_Y=$'\033[38;5;220m'; C_C=$'\033[38;5;75m'
	C_D=$'\033[38;5;67m'; C_B=$'\033[1m'; C_Z=$'\033[0m'
else
	C_G=; C_R=; C_Y=; C_C=; C_D=; C_B=; C_Z=
fi

N_OK=0
N_KO=0
N_WARN=0
KO_LIST=()
CUR_SECTION=

section() {
	CUR_SECTION=$1
	printf '\n%s%s== %s ==%s\n' "$C_B" "$C_C" "$1" "$C_Z"
}

ok() {
	N_OK=$((N_OK + 1))
	printf '  %sOK  %s %s\n' "$C_G" "$C_Z" "$1"
}

# ko NAME EXPECTED GOT CHECK_COMMAND
ko() {
	N_KO=$((N_KO + 1))
	KO_LIST+=("[$CUR_SECTION] $1")
	printf '  %sKO  %s %s\n' "$C_R" "$C_Z" "$1"
	[ -n "${2:-}" ] && printf '       %sexpected:%s %s\n' "$C_D" "$C_Z" "$2"
	[ -n "${3:-}" ] && printf '       %sgot:     %s %s\n' "$C_D" "$C_Z" "$3"
	[ -n "${4:-}" ] && printf '       %scheck:   %s %s\n' "$C_D" "$C_Z" "$4"
	return 0
}

# warn NAME DETAIL CHECK_COMMAND
warn() {
	N_WARN=$((N_WARN + 1))
	printf '  %sWARN%s %s\n' "$C_Y" "$C_Z" "$1"
	[ -n "${2:-}" ] && printf '       %s%s%s\n' "$C_D" "$2" "$C_Z"
	[ -n "${3:-}" ] && printf '       %scheck:   %s %s\n' "$C_D" "$C_Z" "$3"
	return 0
}

info() {
	printf '       %s%s%s\n' "$C_D" "$1" "$C_Z"
}

have() {
	command -v "$1" >/dev/null 2>&1
}

oneline() {
	tr '\n' ' ' | sed -e 's/[[:space:]]\{1,\}/ /g' -e 's/^ //' -e 's/ $//'
}

svc_state() {
	# svc_state is-active|is-enabled unit...  -> first known answer
	local what=$1 u r
	shift
	have systemctl || { echo "no-systemctl"; return; }
	for u in "$@"; do
		r=$(systemctl "$what" "$u" 2>/dev/null | head -n1)
		case $r in
			''|not-found) ;;
			*) echo "$r"; return ;;
		esac
	done
	echo "not-found"
}

need_root_note() {
	[ "$IS_ROOT" = 1 ] || info "(not run as root: this check may be wrong, rerun with sudo)"
}

# --------------------------------------------------------------------------
# context
# --------------------------------------------------------------------------

IS_ROOT=0
[ "$(id -u)" = 0 ] && IS_ROOT=1

LOGIN=${1:-${SUDO_USER:-}}
MON_ARG=${2:-}
if [ -z "$LOGIN" ] || [ "$LOGIN" = root ]; then
	LOGIN=$(getent group user42 2>/dev/null | cut -d: -f4 | cut -d, -f1)
fi
if [ -z "$LOGIN" ]; then
	LOGIN=$(awk -F: '$3 >= 1000 && $3 < 60000 { print $1; exit }' /etc/passwd)
fi

OS_ID=$(sed -n 's/^ID=//p' /etc/os-release 2>/dev/null | tr -d '"' | head -n1)
OS_VER=$(sed -n 's/^VERSION_ID=//p' /etc/os-release 2>/dev/null | tr -d '"' | head -n1)
OS_NAME=$(sed -n 's/^PRETTY_NAME=//p' /etc/os-release 2>/dev/null | tr -d '"' | head -n1)
case $OS_ID in
	debian) FAMILY=debian ;;
	rocky) FAMILY=rocky ;;
	*) FAMILY=other ;;
esac

printf '%s42next Born2beRoot checker%s (subject v1.3, read-only)\n' "$C_B" "$C_Z"
printf 'login: %s   os: %s   root: %s\n' "${LOGIN:-?}" "${OS_NAME:-?}" \
	"$([ "$IS_ROOT" = 1 ] && echo yes || echo 'NO (run: sudo bash check.sh <login>)')"
if [ "$IS_ROOT" != 1 ]; then
	printf '%sWARNING: not root, many checks (sshd -T, ufw, sudoers, shadow) will fail.%s\n' "$C_Y" "$C_Z"
fi

# --------------------------------------------------------------------------
section "operating system"
# --------------------------------------------------------------------------

if [ "$FAMILY" = other ]; then
	ko "OS is Debian or Rocky" "ID=debian or ID=rocky" "ID=${OS_ID:-unknown} ($OS_NAME)" "cat /etc/os-release"
else
	ok "OS is $OS_NAME"
fi

if [ "$FAMILY" = debian ]; then
	dv=$(cat /etc/debian_version 2>/dev/null)
	srcs=$(cat /etc/apt/sources.list /etc/apt/sources.list.d/*.list /etc/apt/sources.list.d/*.sources 2>/dev/null \
		| grep -vE '^[[:space:]]*#' | grep -oiwE 'testing|unstable|sid|experimental' | sort -u | oneline)
	if [ -z "$OS_VER" ] || printf '%s' "$dv" | grep -qE '/|sid'; then
		ko "Debian stable (no testing/unstable)" "stable release" "debian_version=$dv VERSION_ID=${OS_VER:-none}" "cat /etc/debian_version /etc/os-release"
	elif [ -n "$srcs" ]; then
		ko "apt sources use stable only" "no testing/unstable/sid suites" "$srcs" "grep -r . /etc/apt/sources.list*"
	else
		ok "Debian stable ($dv)"
	fi
	info "Debian $OS_VER: the subject asks for the latest stable version, check it is the current one"
elif [ "$FAMILY" = rocky ]; then
	info "Rocky $OS_VER: the subject asks for the latest stable version, check it is the current one"
fi

# --------------------------------------------------------------------------
section "no graphical interface"
# --------------------------------------------------------------------------

GUI_RE='^(xserver-xorg|xserver-xorg-core|xorg|xwayland|xvfb|weston|gnome-shell|gnome-session|mutter|kwin|kwin-x11|kwin-wayland|kwin_wayland|plasma-workspace|sway|wayfire|labwc|hyprland|gdm|gdm3|sddm|lightdm|xdm|lxdm|slim|xfwm4|xfce4-session|openbox|i3|i3-wm|task-desktop|task-[a-z]+-desktop|xorg-x11-server-xorg|xorg-x11-server-xwayland|xorg-x11-server-xvfb|xorg-x11-server-xephyr|xorg-x11-server-xnest)$'
if have dpkg-query; then
	pkgs=$(dpkg-query -W -f='${db:Status-Abbrev} ${Package}\n' 2>/dev/null | awk '$1 ~ /^ii/ { print $2 }')
	pkgcmd="dpkg -l | grep -Ei 'xorg|wayland|gdm|lightdm|sddm|gnome|kde|xfce'"
elif have rpm; then
	pkgs=$(rpm -qa --qf '%{NAME}\n' 2>/dev/null)
	pkgcmd="rpm -qa | grep -Ei 'xorg|wayland|gdm|lightdm|sddm|gnome|kde|xfce'"
else
	pkgs=
	pkgcmd="(no dpkg / rpm)"
fi
gui=$(printf '%s\n' "$pkgs" | awk '{ print tolower($0) }' | grep -E "$GUI_RE" | sort -u)
if [ -n "$gui" ]; then
	ko "no X.org / Wayland / display server installed (grade 0 otherwise)" "none" \
		"$(printf '%s' "$gui" | head -n 12 | oneline)$([ "$(printf '%s\n' "$gui" | wc -l)" -gt 12 ] && echo ' ...')" "$pkgcmd"
else
	ok "no X.org / Wayland / display manager package installed"
fi
procs=$(ps -eo comm= 2>/dev/null | grep -xE 'Xorg|X|Xwayland|gnome-shell|weston|kwin_wayland|kwin_x11|sway|gdm|gdm3|sddm|lightdm' | sort -u | oneline)
if [ -n "$procs" ]; then
	ko "no graphical server running" "none" "$procs" "ps -e | grep -Ei 'xorg|wayland|gdm|gnome-shell'"
else
	ok "no graphical server process running"
fi
tgt=$(systemctl get-default 2>/dev/null)
if [ "$tgt" = graphical.target ]; then
	warn "default systemd target is graphical.target" "a server should boot to multi-user.target" "systemctl get-default"
elif [ -n "$tgt" ]; then
	ok "default target: $tgt"
fi

# --------------------------------------------------------------------------
section "encrypted LVM partitions"
# --------------------------------------------------------------------------

if ! have lsblk; then
	ko "lsblk available" "lsblk" "not found" "lsblk"
else
	crypts=$(lsblk -rno NAME,TYPE 2>/dev/null | awk '$2 == "crypt" { print $1 }')
	ncrypt=$(printf '%s' "$crypts" | grep -c .)
	lvs_all=$(lsblk -rno NAME,TYPE 2>/dev/null | awk '$2 == "lvm" { print $1 }' | sort -u)
	nlv=$(printf '%s' "$lvs_all" | grep -c .)
	enc_lv=
	for c in $crypts; do
		enc_lv="$enc_lv
$(lsblk -rno NAME,TYPE "/dev/mapper/$c" 2>/dev/null | awk '$2 == "lvm" { print $1 }')"
	done
	enc_lv=$(printf '%s\n' "$enc_lv" | grep . | sort -u)
	nenc=$(printf '%s' "$enc_lv" | grep -c .)
	if [ "$ncrypt" -ge 1 ]; then
		ok "encrypted volume (crypt): $(printf '%s' "$crypts" | oneline)"
	else
		ko "at least one LUKS (crypt) volume" "TYPE crypt in lsblk" "no crypt device" "lsblk"
	fi
	if [ "$nenc" -ge 2 ]; then
		ok "$nenc LVM logical volumes inside the encrypted volume"
	else
		ko "at least 2 encrypted partitions using LVM" ">= 2 lvm under a crypt device" \
			"$nenc encrypted LV(s), $nlv LV(s) in total" "lsblk"
	fi
	mounts=$(lsblk -rno TYPE,MOUNTPOINT 2>/dev/null | awk '$1 == "lvm" { print $2 }')
	missing=
	for mp in / /home /var /srv /tmp /var/log "[SWAP]"; do
		printf '%s\n' "$mounts" | grep -qxF "$mp" || missing="$missing $mp"
	done
	# v1.3 has no bonus part and its partitioning is only an example:
	# shown for information, never a WARN
	if [ -n "$missing" ]; then
		info "not on LVM (old bonus layout, not required by v1.3):$missing (LVs: $(printf '%s' "$lvs_all" | oneline))"
	else
		info "/ swap /home /var /srv /tmp /var/log all on LVM"
	fi
fi

# --------------------------------------------------------------------------
if [ "$FAMILY" = rocky ]; then
	section "SELinux"
	mode=$(getenforce 2>/dev/null)
	if [ "$mode" = Enforcing ]; then
		ok "SELinux running (Enforcing)"
	else
		ko "SELinux running" "Enforcing" "${mode:-getenforce not found}" "getenforce; sestatus"
	fi
	cfg=$(sed -n 's/^[[:space:]]*SELINUX=//p' /etc/selinux/config 2>/dev/null | tail -n1)
	if [ "$cfg" = enforcing ]; then
		ok "SELinux enabled at startup (/etc/selinux/config SELINUX=enforcing)"
	else
		ko "SELinux enabled at startup" "SELINUX=enforcing" "SELINUX=${cfg:-?}" "grep ^SELINUX= /etc/selinux/config"
	fi
	if grep -qwE 'selinux=0|enforcing=0' /proc/cmdline 2>/dev/null; then
		ko "kernel command line does not disable SELinux" "no selinux=0 / enforcing=0" "$(cat /proc/cmdline)" "cat /proc/cmdline"
	fi
	if have semanage; then
		if semanage port -l 2>/dev/null | grep -E '^ssh_port_t' | grep -qw "$SSH_PORT"; then
			ok "SELinux allows sshd on $SSH_PORT (ssh_port_t)"
		else
			ko "SELinux policy adapted: port $SSH_PORT in ssh_port_t" "ssh_port_t tcp $SSH_PORT, 22" \
				"$(semanage port -l 2>/dev/null | grep -E '^ssh_port_t' | oneline)" "semanage port -l | grep ssh"
		fi
	else
		warn "semanage not installed, cannot check ssh_port_t" "" "dnf provides semanage"
	fi
else
	section "AppArmor"
	kp=$(cat /sys/module/apparmor/parameters/enabled 2>/dev/null)
	if [ "$kp" = Y ]; then
		ok "AppArmor enabled in the kernel"
	else
		ko "AppArmor enabled in the kernel" "Y" "${kp:-no apparmor module}" "cat /sys/module/apparmor/parameters/enabled"
	fi
	if grep -qwE 'apparmor=0' /proc/cmdline 2>/dev/null; then
		ko "kernel command line does not disable AppArmor" "no apparmor=0" "$(cat /proc/cmdline)" "cat /proc/cmdline"
	fi
	st=$(svc_state is-enabled apparmor)
	if [ "$st" = enabled ]; then
		ok "apparmor.service enabled at startup"
	else
		ko "apparmor.service enabled at startup" "enabled" "$st" "systemctl is-enabled apparmor"
	fi
	st=$(svc_state is-active apparmor)
	if [ "$st" = active ]; then
		ok "apparmor.service active"
	else
		ko "apparmor.service active" "active" "$st" "systemctl status apparmor"
	fi
	if have aa-status && [ "$IS_ROOT" = 1 ]; then
		np=$(aa-status --profiled 2>/dev/null)
		if aa-status --enabled 2>/dev/null; then
			ok "aa-status: enabled, ${np:-?} profile(s) loaded"
		else
			ko "aa-status --enabled" "exit 0" "not enabled" "sudo aa-status"
		fi
	fi
fi

# --------------------------------------------------------------------------
section "SSH"
# --------------------------------------------------------------------------

st=$(svc_state is-active ssh sshd)
if [ "$st" = active ]; then
	ok "SSH service running"
else
	ko "SSH service running" "active" "$st" "systemctl status ssh   (Rocky: sshd)"
fi
st=$(svc_state is-enabled ssh sshd)
case $st in
	enabled|alias|static|indirect) ok "SSH service enabled at boot ($st)" ;;
	*) ko "SSH service enabled at boot" "enabled" "$st" "systemctl is-enabled ssh" ;;
esac

ports=
prl=
if have sshd && [ "$IS_ROOT" = 1 ] && sshd -T >/dev/null 2>&1; then
	ports=$(sshd -T 2>/dev/null | awk '$1 == "port" { print $2 }' | sort -u | oneline)
	prl=$(sshd -T 2>/dev/null | awk '$1 == "permitrootlogin" { print $2 }' | head -n1)
	src="sshd -T"
else
	cfgs=$(ls /etc/ssh/sshd_config /etc/ssh/sshd_config.d/*.conf 2>/dev/null)
	# first value wins in sshd_config (included files come first on Debian)
	# shellcheck disable=SC2086
	ports=$(awk 'tolower($1) == "port" { print $2 }' $cfgs 2>/dev/null | sort -u | oneline)
	# shellcheck disable=SC2086
	prl=$(awk 'tolower($1) == "permitrootlogin" { print tolower($2); exit }' $cfgs 2>/dev/null)
	src="sshd_config (grep)"
	need_root_note
fi
if [ "$ports" = "$SSH_PORT" ]; then
	ok "sshd configured on port $SSH_PORT only ($src)"
else
	ko "sshd configured on port $SSH_PORT only" "$SSH_PORT" "${ports:-none (default 22)}" "sudo sshd -T | grep -w port"
fi
if [ "$prl" = no ]; then
	ok "PermitRootLogin no"
else
	ko "root cannot log in through SSH" "PermitRootLogin no" "PermitRootLogin ${prl:-unset (default prohibit-password)}" \
		"sudo sshd -T | grep -i permitrootlogin"
fi

if have ss; then
	if [ "$IS_ROOT" = 1 ]; then
		lports=$(ss -Htlnp 2>/dev/null | grep -E '"sshd|"systemd"' | awk '{ n = split($4, a, ":"); print a[n] }' | sort -un | oneline)
	else
		lports=$(ss -Htln 2>/dev/null | awk '{ n = split($4, a, ":"); print a[n] }' | grep -xE "22|$SSH_PORT" | sort -un | oneline)
	fi
	if [ "$lports" = "$SSH_PORT" ]; then
		ok "sshd listening on $SSH_PORT only"
	else
		ko "sshd listening on $SSH_PORT only" "$SSH_PORT" "${lports:-nothing listening}" "sudo ss -tlnp | grep ssh"
	fi
fi

# --------------------------------------------------------------------------
section "firewall"
# --------------------------------------------------------------------------

if [ "$FAMILY" = rocky ]; then
	if ! have firewall-cmd; then
		ko "firewalld installed" "firewall-cmd" "not found" "dnf list installed firewalld"
	else
		state=$(firewall-cmd --state 2>&1 | head -n1)
		if [ "$state" = running ]; then
			ok "firewalld running"
		else
			ko "firewalld running" "running" "$state" "sudo firewall-cmd --state"
		fi
		st=$(svc_state is-enabled firewalld)
		[ "$st" = enabled ] && ok "firewalld enabled at boot" || ko "firewalld enabled at boot" "enabled" "$st" "systemctl is-enabled firewalld"
		zones=$(firewall-cmd --get-active-zones 2>/dev/null | grep -v '^[[:space:]]' | oneline)
		[ -z "$zones" ] && zones=$(firewall-cmd --get-default-zone 2>/dev/null)
		for z in $zones; do
			p=$(firewall-cmd --zone="$z" --list-ports 2>/dev/null | oneline)
			s=$(firewall-cmd --zone="$z" --list-services 2>/dev/null | oneline)
			if [ "$p" = "$SSH_PORT/tcp" ]; then
				ok "zone $z: only $SSH_PORT/tcp in ports"
			else
				ko "zone $z: only port $SSH_PORT open" "$SSH_PORT/tcp" "ports: ${p:-none}" "sudo firewall-cmd --zone=$z --list-all"
			fi
			bad=$(printf '%s\n' $s | grep -vx 'dhcpv6-client' | oneline)
			if [ -n "$bad" ]; then
				ko "zone $z: no service opening other ports" "no services (ssh = port 22)" "services: $s" \
					"sudo firewall-cmd --zone=$z --list-services"
			elif [ -n "$s" ]; then
				warn "zone $z: service $s still allowed" "dhcpv6-client opens udp/546" "sudo firewall-cmd --zone=$z --list-all"
			else
				ok "zone $z: no extra service"
			fi
		done
	fi
else
	if ! have ufw; then
		ko "UFW installed" "ufw" "not found" "dpkg -l ufw"
	else
		if [ "$IS_ROOT" = 1 ]; then
			us=$(ufw status verbose 2>&1)
			if printf '%s\n' "$us" | grep -q '^Status: active'; then
				ok "UFW active"
			else
				ko "UFW active" "Status: active" "$(printf '%s\n' "$us" | head -n1)" "sudo ufw status verbose"
			fi
			rules=$(printf '%s\n' "$us" | sed -n '/^--/,$p' | sed '1d' | grep -E 'ALLOW|LIMIT')
			allowed=$(printf '%s\n' "$rules" | grep . | awk '{ print $1 }' | sed 's#/.*##' | sort -u | oneline)
			if [ "$allowed" = "$SSH_PORT" ]; then
				ok "UFW: only $SSH_PORT allowed"
			else
				ko "UFW: only port $SSH_PORT open" "$SSH_PORT (and $SSH_PORT (v6))" "${allowed:-no ALLOW rule}" "sudo ufw status numbered"
			fi
		else
			need_root_note
		fi
		en=$(sed -n 's/^ENABLED=//p' /etc/ufw/ufw.conf 2>/dev/null)
		if [ "$en" = yes ]; then
			ok "UFW active at startup (/etc/ufw/ufw.conf ENABLED=yes)"
		else
			ko "UFW active at startup" "ENABLED=yes" "ENABLED=${en:-?}" "grep ENABLED /etc/ufw/ufw.conf"
		fi
		st=$(svc_state is-enabled ufw)
		[ "$st" = enabled ] && ok "ufw.service enabled" || ko "ufw.service enabled" "enabled" "$st" "systemctl is-enabled ufw"
	fi
fi

# --------------------------------------------------------------------------
section "hostname, user and groups"
# --------------------------------------------------------------------------

want="${LOGIN}42"
hn=$(hostname 2>/dev/null || cat /proc/sys/kernel/hostname)
if [ "$hn" = "$want" ]; then
	ok "hostname is $want"
else
	ko "hostname is login + 42" "$want" "$hn" "hostnamectl"
fi
fhn=$(cat /etc/hostname 2>/dev/null)
[ "$fhn" = "$want" ] || ko "/etc/hostname" "$want" "$fhn" "cat /etc/hostname"
if ! grep -qw "$hn" /etc/hosts 2>/dev/null; then
	warn "hostname $hn not in /etc/hosts" "sudo will print 'unable to resolve host'" "cat /etc/hosts"
fi

if [ -z "$LOGIN" ] || ! id "$LOGIN" >/dev/null 2>&1; then
	ko "user with your login exists" "user '${LOGIN:-<login>}'" "not found" "getent passwd ${LOGIN:-<login>}"
else
	ok "user $LOGIN exists"
	ug=" $(id -nG "$LOGIN" 2>/dev/null) "
	if getent group user42 >/dev/null; then
		ok "group user42 exists"
	else
		ko "group user42 exists" "user42" "missing" "getent group user42"
	fi
	for g in user42 sudo; do
		case $ug in
			*" $g "*) ok "$LOGIN in group $g" ;;
			*) ko "$LOGIN belongs to group $g" "$g" "groups:$ug" "groups $LOGIN" ;;
		esac
	done
fi

# --------------------------------------------------------------------------
section "password policy: login.defs"
# --------------------------------------------------------------------------

defs() {
	awk -v k="$1" '$1 == k { v = $2 } END { print v }' /etc/login.defs 2>/dev/null
}
for pair in "PASS_MAX_DAYS $PASS_MAX" "PASS_MIN_DAYS $PASS_MIN" "PASS_WARN_AGE $PASS_WARN"; do
	k=${pair% *}; v=${pair#* }
	got=$(defs "$k")
	if [ "$got" = "$v" ]; then
		ok "$k $v"
	else
		ko "/etc/login.defs $k" "$v" "${got:-unset}" "grep ^PASS_ /etc/login.defs"
	fi
done

# --------------------------------------------------------------------------
section "password policy: pwquality"
# --------------------------------------------------------------------------

if [ "$FAMILY" = rocky ]; then
	PAMFILES="/etc/pam.d/system-auth /etc/pam.d/password-auth"
else
	PAMFILES="/etc/pam.d/common-password"
fi
pamline=
for f in $PAMFILES; do
	l=$(grep -E '^[[:space:]]*password[[:space:]].*pam_(pwquality|cracklib)\.so' "$f" 2>/dev/null | head -n1)
	if [ -n "$l" ]; then
		pamline=$l
		pamfile=$f
		break
	fi
done
if [ -n "$pamline" ]; then
	ok "pam_pwquality used in $pamfile"
	info "$pamline"
	printf '%s' "$pamline" | grep -q pam_cracklib && warn "pam_cracklib used instead of pam_pwquality" "" "cat $pamfile"
else
	ko "pam_pwquality in the PAM password stack" "password requisite pam_pwquality.so ..." "not found in $PAMFILES" \
		"grep pwquality $PAMFILES   (Debian: apt install libpam-pwquality)"
fi
PAMARGS=$(printf '%s' "$pamline" | sed -E 's/.*pam_(pwquality|cracklib)\.so//')
PWQ_CONF=$(cat /etc/security/pwquality.conf.d/*.conf /etc/security/pwquality.conf 2>/dev/null | sed 's/#.*//')

# pwq KEY -> value from the PAM line (wins), else pwquality.conf, else empty
pwq() {
	local v
	v=$(printf '%s\n' $PAMARGS | sed -n "s/^$1=//p" | tail -n1)
	[ -n "$v" ] && { echo "$v"; return; }
	printf '%s\n' "$PWQ_CONF" | sed -n "s/^[[:space:]]*$1[[:space:]]*=[[:space:]]*\([^[:space:]]*\).*/\1/p" | tail -n1
}
pwq_flag() {
	printf '%s\n' $PAMARGS | grep -qx "$1" && return 0
	printf '%s\n' "$PWQ_CONF" | grep -qE "^[[:space:]]*$1[[:space:]]*$"
}
PWQ_CHECK="grep -v '^#' /etc/security/pwquality.conf; cat $PAMFILES"

v=$(pwq minlen)
if [ -n "$v" ] && [ "$v" -ge "$PW_MINLEN" ] 2>/dev/null; then
	ok "minlen=$v (at least $PW_MINLEN characters)"
else
	ko "password at least $PW_MINLEN characters" "minlen=$PW_MINLEN" "minlen=${v:-unset (default 8)}" "$PWQ_CHECK"
fi
for c in "ucredit uppercase_letter" "lcredit lowercase_letter" "dcredit digit"; do
	k=${c% *}; what=${c#* }; what=${what/_/ }
	v=$(pwq "$k")
	if [ -n "$v" ] && [ "$v" -le -1 ] 2>/dev/null; then
		ok "$k=$v (at least one $what)"
	else
		ko "password contains a $what" "$k=-1" "$k=${v:-unset (default 0)}" "$PWQ_CHECK"
	fi
done
v=$(pwq maxrepeat)
if [ -n "$v" ] && [ "$v" -ge 1 ] 2>/dev/null && [ "$v" -le "$PW_MAXREPEAT" ]; then
	ok "maxrepeat=$v (no more than $PW_MAXREPEAT identical consecutive characters)"
else
	ko "max $PW_MAXREPEAT consecutive identical characters" "maxrepeat=$PW_MAXREPEAT" "maxrepeat=${v:-unset (default 0 = off)}" "$PWQ_CHECK"
fi
v=$(pwq usercheck)
if pwq_flag reject_username; then
	ok "reject_username (password must not include the user name)"
elif [ -n "$v" ] && [ "$v" != 0 ]; then
	ok "usercheck=$v (password must not include the user name)"
elif [ "$v" = 0 ]; then
	ko "password must not include the user name" "usercheck=1 or reject_username" "usercheck=0" "$PWQ_CHECK"
else
	warn "user name check only by default (usercheck defaults to 1)" "graders often want reject_username or usercheck=1 written explicitly" "$PWQ_CHECK"
fi
v=$(pwq difok)
if [ -n "$v" ] && [ "$v" -ge "$PW_DIFOK" ] 2>/dev/null; then
	ok "difok=$v ($PW_DIFOK characters not in the previous password; never applied to root by pwquality)"
else
	ko "at least $PW_DIFOK characters not in the previous password" "difok=$PW_DIFOK" "difok=${v:-unset (default 1)}" "$PWQ_CHECK"
fi
if pwq_flag enforce_for_root; then
	ok "enforce_for_root (root password follows the policy)"
else
	ko "root password has to comply with the policy" "enforce_for_root" "missing" "$PWQ_CHECK"
fi

# --------------------------------------------------------------------------
section "password policy: existing accounts (chage)"
# --------------------------------------------------------------------------

if [ "$IS_ROOT" != 1 ] || [ ! -r /etc/shadow ]; then
	ko "read /etc/shadow (chage -l)" "root" "not readable" "sudo chage -l root; sudo chage -l $LOGIN"
else
	cfg_day=0
	for f in /etc/login.defs /etc/security/pwquality.conf $PAMFILES; do
		[ -e "$f" ] || continue
		d=$(( $(stat -c %Y "$f") / 86400 ))
		[ "$d" -gt "$cfg_day" ] && cfg_day=$d
	done
	humans=$(awk -F: '$3 >= 1000 && $3 < 60000 && $7 !~ /(nologin|false)$/ { print $1 }' /etc/passwd)
	for u in root $LOGIN $humans; do
		case " $seen " in *" $u "*) continue ;; esac
		seen="$seen $u"
		line=$(awk -F: -v u="$u" '$1 == u' /etc/shadow)
		[ -z "$line" ] && continue
		lc=$(printf '%s' "$line" | cut -d: -f3)
		mn=$(printf '%s' "$line" | cut -d: -f4)
		mx=$(printf '%s' "$line" | cut -d: -f5)
		wa=$(printf '%s' "$line" | cut -d: -f6)
		if [ "$u" = root ] || [ "$u" = "$LOGIN" ]; then
			report=ko
		else
			report=warn
		fi
		if [ "$mn/$mx/$wa" = "$PASS_MIN/$PASS_MAX/$PASS_WARN" ]; then
			ok "$u: min $mn / max $mx / warn $wa days"
		elif [ "$report" = ko ]; then
			ko "chage applied to $u (login.defs only affects new users)" "min $PASS_MIN / max $PASS_MAX / warn $PASS_WARN" \
				"min ${mn:-?} / max ${mx:-?} / warn ${wa:-?}" "sudo chage -l $u"
		else
			warn "user $u: min ${mn:-?} / max ${mx:-?} / warn ${wa:-?} (expected $PASS_MIN/$PASS_MAX/$PASS_WARN)" "" "sudo chage -l $u"
		fi
		if [ -z "$lc" ] || [ "$lc" = 0 ]; then
			warn "$u: password never changed / must change at next login" "" "sudo chage -l $u"
		elif [ "$lc" -lt "$cfg_day" ] 2>/dev/null; then
			warn "$u: password last changed before the policy files were modified" \
				"the subject asks to change every password (root included) after configuring" "sudo chage -l $u"
		fi
	done
fi

# --------------------------------------------------------------------------
section "sudo"
# --------------------------------------------------------------------------

if ! have sudo; then
	ko "sudo installed" "sudo" "not found" "which sudo"
else
	ok "sudo installed"
fi
SUDOERS=/etc/sudoers
for f in /etc/sudoers.d/*; do
	[ -f "$f" ] || continue
	b=${f##*/}
	case $b in
		*.*|*~)
			grep -q '^[[:space:]]*Defaults' "$f" 2>/dev/null &&
				warn "$f is IGNORED by sudo (name contains '.' or ends with '~')" "" "ls /etc/sudoers.d" ;;
		*) SUDOERS="$SUDOERS $f" ;;
	esac
done
if [ "$IS_ROOT" != 1 ] || [ ! -r /etc/sudoers ]; then
	ko "read sudoers" "root" "not readable" "sudo cat /etc/sudoers /etc/sudoers.d/*"
fi
# shellcheck disable=SC2086
DEF=$(grep -hE '^[[:space:]]*Defaults' $SUDOERS 2>/dev/null)
if [ "$IS_ROOT" = 1 ] && have visudo; then
	if visudo -c >/dev/null 2>&1; then
		ok "visudo -c: sudoers syntax OK"
	else
		ko "sudoers syntax" "parsed OK" "$(visudo -c 2>&1 | grep -vi 'parsed OK' | head -n2 | oneline)" "sudo visudo -c"
	fi
fi
SUDO_CHECK="sudo grep -h Defaults /etc/sudoers /etc/sudoers.d/*"

# sval KEY -> last value of KEY=... in the Defaults lines
sval() {
	printf '%s\n' "$DEF" | grep -oE "(^|[[:space:],])$1[[:space:]]*=[[:space:]]*(\"[^\"]*\"|[^,[:space:]]+)" \
		| tail -n1 | sed -E "s/^[[:space:],]*$1[[:space:]]*=[[:space:]]*//"
}
sflag() {
	printf '%s\n' "$DEF" | grep -qE "(^|[[:space:],])$1([[:space:],]|$)" &&
		! printf '%s\n' "$DEF" | grep -qE "!$1([[:space:],]|$)"
}

v=$(sval passwd_tries)
if [ "$v" = "$SUDO_TRIES" ]; then
	ok "passwd_tries=$v"
elif [ -z "$v" ]; then
	warn "passwd_tries not set (sudo default is 3)" "write Defaults passwd_tries=$SUDO_TRIES explicitly for the defense" "$SUDO_CHECK"
else
	ko "sudo limited to $SUDO_TRIES attempts" "passwd_tries=$SUDO_TRIES" "passwd_tries=$v" "$SUDO_CHECK"
fi
v=$(sval badpass_message)
if [ -n "$v" ]; then
	ok "badpass_message=$v"
else
	ko "custom message on wrong sudo password" "Defaults badpass_message=\"...\"" "not set" "$SUDO_CHECK"
fi
v=$(sval logfile | tr -d '"')
case $v in
	"$SUDO_LOGDIR"?*)
		ok "logfile=$v"
		if [ -e "$v" ]; then ok "log file $v exists"; else warn "$v does not exist yet (created on the first sudo)" "" "ls -l $SUDO_LOGDIR"; fi ;;
	*) ko "sudo log file in $SUDO_LOGDIR" "Defaults logfile=\"${SUDO_LOGDIR}sudo.log\"" "logfile=${v:-unset}" "$SUDO_CHECK" ;;
esac
for fl in log_input log_output; do
	if sflag "$fl"; then
		ok "$fl"
	else
		ko "sudo logs every action ($fl)" "Defaults $fl" "missing or negated" "$SUDO_CHECK"
	fi
done
v=$(sval iolog_dir | tr -d '"')
case $v in
	"${SUDO_LOGDIR%/}"|"$SUDO_LOGDIR"*) ok "iolog_dir=$v" ;;
	'') warn "iolog_dir not set: input/output logs go to /var/log/sudo-io" "set Defaults iolog_dir=\"$SUDO_LOGDIR\" to keep everything in $SUDO_LOGDIR" "$SUDO_CHECK" ;;
	*) warn "iolog_dir=$v is outside $SUDO_LOGDIR" "" "$SUDO_CHECK" ;;
esac
if sflag requiretty; then
	ok "requiretty (TTY mode)"
else
	ko "TTY mode enabled" "Defaults requiretty" "missing or negated" "$SUDO_CHECK"
fi
v=$(sval secure_path | tr -d '"')
if [ "$v" = "$SECURE_PATH" ]; then
	ok "secure_path=$v"
elif [ -n "$v" ]; then
	warn "secure_path differs from the subject example" "expected: $SECURE_PATH
       got:      $v" "$SUDO_CHECK"
else
	ko "sudo paths restricted" "Defaults secure_path=\"$SECURE_PATH\"" "not set" "$SUDO_CHECK"
fi
if [ "$IS_ROOT" = 1 ] && [ -n "$LOGIN" ] && id "$LOGIN" >/dev/null 2>&1; then
	if sudo -l -U "$LOGIN" 2>/dev/null | grep -q 'may run the following'; then
		ok "$LOGIN can use sudo"
	else
		ko "$LOGIN can use sudo" "rules for $LOGIN" "$LOGIN may not run sudo" "sudo -l -U $LOGIN"
	fi
fi

# --------------------------------------------------------------------------
section "monitoring.sh: cron"
# --------------------------------------------------------------------------

st=$(svc_state is-active cron crond)
if [ "$st" = active ]; then
	ok "cron service running"
else
	ko "cron service running" "active" "$st" "systemctl status cron   (Rocky: crond)"
fi

# crontab lines mentioning monitoring: "<source>|<line>"
cronlines=
for f in /var/spool/cron/crontabs/* /var/spool/cron/*; do
	[ -f "$f" ] && [ -r "$f" ] || continue
	cronlines="$cronlines$(grep -vE '^[[:space:]]*#' "$f" | grep -i monitoring | sed "s#^#user:${f##*/}|#")
"
done
for f in /etc/crontab /etc/cron.d/*; do
	[ -f "$f" ] && [ -r "$f" ] || continue
	cronlines="$cronlines$(grep -vE '^[[:space:]]*#' "$f" | grep -i monitoring | sed "s#^#system:$f|#")
"
done
cronlines=$(printf '%s\n' "$cronlines" | grep .)
[ "$IS_ROOT" = 1 ] || need_root_note

script=
every_ok=0
reboot_ok=0
if [ -z "$cronlines" ]; then
	ko "cron entry running monitoring.sh" "*/$CRON_EVERY * * * * /path/monitoring.sh" "none found" "sudo crontab -l; ls /etc/cron.d"
else
	while IFS= read -r cl; do
		src=${cl%%|*}
		line=${cl#*|}
		info "$src: $line"
		[ -z "$script" ] && script=$(printf '%s' "$line" | grep -oE '[^[:space:];|&<>"'"'"']*monitoring\.sh' | head -n1)
		# split into fields without expanding the * of the cron fields
		set -f
		# shellcheck disable=SC2086
		set -- $line
		set +f
		case $1 in
			@reboot) reboot_ok=1; continue ;;
			@*) continue ;;
		esac
		m=$1; rest="$2 $3 $4 $5"
		case $m in
			"*/$CRON_EVERY"|"0-59/$CRON_EVERY"|"0,10,20,30,40,50") [ "$rest" = "* * * *" ] && every_ok=1 ;;
		esac
	done <<EOF
$cronlines
EOF
	if [ "$every_ok" = 1 ]; then
		ok "monitoring.sh runs every $CRON_EVERY minutes"
	else
		ko "monitoring.sh runs every $CRON_EVERY minutes" "*/$CRON_EVERY * * * *" "see the line(s) above" "sudo crontab -l"
	fi
	if [ "$reboot_ok" = 1 ]; then
		ok "@reboot entry (display at server startup)"
	else
		warn "no @reboot entry" "the subject asks for a display at server startup too" "sudo crontab -l"
	fi
fi
[ -n "$MON_ARG" ] && script=$MON_ARG
if [ -z "$script" ] || [ ! -f "$script" ]; then
	for f in /usr/local/bin/monitoring.sh /root/monitoring.sh /home/*/monitoring.sh /monitoring.sh /usr/bin/monitoring.sh; do
		[ -f "$f" ] && { script=$f; break; }
	done
fi

# --------------------------------------------------------------------------
section "monitoring.sh: script"
# --------------------------------------------------------------------------

if [ -z "$script" ] || [ ! -f "$script" ]; then
	ko "monitoring.sh found" "a file named monitoring.sh" "not found" "sudo find / -xdev -name monitoring.sh"
else
	ok "found $script"
	# cron line "bash /path/monitoring.sh": the file needs neither +x nor a
	# shebang to run with bash. Run directly, it needs both.
	via_bash=0
	printf '%s\n' "$cronlines" | grep -qE '(^|[[:space:]/|;&])bash[[:space:]]+[^|;&]*monitoring\.sh' && via_bash=1
	if [ -x "$script" ]; then
		ok "executable"
	elif [ "$via_bash" = 1 ]; then
		warn "monitoring.sh not executable (cron runs it with bash, so it still runs)" "" "ls -l $script"
	else
		ko "monitoring.sh executable (or run by cron as: bash monitoring.sh)" "-rwx..." "$(ls -l "$script")" "ls -l $script"
	fi
	sb=$(head -n1 "$script")
	case $sb in
		'#!'*bash*) ok "written in bash ($sb)" ;;
		*)
			if [ "$via_bash" = 1 ]; then
				warn "no bash shebang (cron runs it with bash)" "first line: ${sb:-empty}" "head -n1 $script"
			else
				ko "written in bash" "#!/bin/bash" "${sb:-empty first line}" "head -n1 $script"
			fi ;;
	esac
	if grep -qw wall "$script" || printf '%s\n' "$cronlines" | grep -qw wall; then
		ok "uses wall (display on all terminals)"
	else
		warn "wall not found in the script nor in the cron line" "the subject suggests wall to display on all terminals" "grep wall $script"
	fi
	if have bash && bash -n "$script" 2>/dev/null; then
		ok "bash -n: no syntax error"
	else
		ko "bash syntax" "no error" "$(bash -n "$script" 2>&1 | head -n2 | oneline)" "bash -n $script"
	fi
fi

# --------------------------------------------------------------------------
section "monitoring.sh: output"
# --------------------------------------------------------------------------

if [ -n "$script" ] && [ -f "$script" ]; then
	wall() {
		while [ $# -gt 0 ]; do
			case $1 in
				--) shift; break ;;
				-t|--timeout|-g|--group) shift 2 ;;
				-*) shift ;;
				*) break ;;
			esac
		done
		if [ $# -gt 0 ]; then
			printf '%s\n' "$*"
		else
			cat
		fi
	}
	export -f wall
	# `command -v wall`, `env wall`, `sudo wall` do not see the function:
	# put a stand-in wall (and a sudo that just runs its command, we are
	# root already) first in PATH
	fakebin=$(mktemp -d 2>/dev/null)
	runpath=$PATH
	if [ -n "$fakebin" ] && [ -d "$fakebin" ]; then
		{
			echo '#!/bin/bash'
			declare -f wall
			echo 'wall "$@"'
		} > "$fakebin/wall"
		cat > "$fakebin/sudo" <<'SUDO'
#!/bin/bash
while [ $# -gt 0 ]; do
	case $1 in
		--) shift; break ;;
		-u|-g|-C|-h|-p|-U|-r|-t|-D|--user|--group|--host|--prompt) shift 2 ;;
		-*) shift ;;
		*) break ;;
	esac
done
[ $# -gt 0 ] && exec "$@"
SUDO
		chmod 755 "$fakebin/wall" "$fakebin/sudo"
		runpath=$fakebin:$PATH
	fi
	# run an in-memory copy where /usr/bin/wall is replaced by the function
	code=$(sed -E 's#(/usr)?/s?bin/wall#wall#g' "$script")
	if have timeout; then TO="timeout 60"; else TO=; fi
	raw=$(PATH=$runpath $TO bash -c "$code" "$script" 2> >(sed 's/^/__STDERR__ /') </dev/null)
	rc=$?
	unset -f wall
	[ -n "$fakebin" ] && rm -rf "$fakebin"
	raw=$(printf '%s\n' "$raw" | sed 's/\x1b\[[0-9;]*[a-zA-Z]//g')
	out=$(printf '%s\n' "$raw" | grep -v '^__STDERR__ ')
	err=$(printf '%s\n' "$raw" | sed -n 's/^__STDERR__ //p')
	if [ "$rc" = 124 ]; then
		warn "monitoring.sh still running after 60 s (stopped here)" "the output below may be incomplete" "time bash $script"
	fi
	printf '%s\n' "$out" | sed 's/^/       | /'
	if [ -n "$err" ]; then
		ko "no errors displayed" "empty stderr" "$(printf '%s' "$err" | head -n3 | oneline)" "bash $script >/dev/null"
	else
		ok "no errors displayed"
	fi

	line_of() {
		printf '%s\n' "$out" | grep -iE "$1" | { if [ -n "${2:-}" ]; then grep -viE "$2"; else cat; fi; } | head -n1
	}
	NUM='[0-9]+([.,][0-9]+)?'

	# 1. architecture + kernel
	l=$(line_of 'arch')
	if [ -n "$l" ] && printf '%s' "$l" | grep -qF "$(uname -m)" && printf '%s' "$l" | grep -qF "$(uname -r)"; then
		ok "architecture + kernel version"
	else
		ko "architecture and kernel version" "line with $(uname -m) and $(uname -r)" "${l:-no 'Architecture' line}" "uname -a"
	fi
	# 2. physical CPUs
	exp_p=$(grep '^physical id' /proc/cpuinfo 2>/dev/null | sort -u | wc -l)
	[ "$exp_p" -eq 0 ] && exp_p=1
	l=$(line_of 'physical|pcpu' 'vcpu|virtual')
	g=$(printf '%s' "$l" | grep -oE '[0-9]+' | tail -n1)
	if [ -z "$g" ]; then
		ko "number of physical processors" "#Physical CPU: $exp_p" "${l:-no 'Physical CPU' line}" "grep 'physical id' /proc/cpuinfo | sort -u | wc -l"
	elif [ "$g" = "$exp_p" ]; then
		ok "physical CPU: $g"
	else
		warn "physical CPU shows $g, /proc/cpuinfo says $exp_p" "$l" "grep 'physical id' /proc/cpuinfo | sort -u | wc -l"
	fi
	# 3. vCPU
	exp_v=$(grep -c '^processor' /proc/cpuinfo 2>/dev/null)
	l=$(line_of 'vcpu|virtual')
	g=$(printf '%s' "$l" | grep -oE '[0-9]+' | tail -n1)
	if [ -z "$g" ]; then
		ko "number of virtual processors" "#vCPU: $exp_v" "${l:-no 'vCPU' line}" "grep -c ^processor /proc/cpuinfo"
	elif [ "$g" = "$exp_v" ]; then
		ok "vCPU: $g"
	else
		warn "vCPU shows $g, /proc/cpuinfo says $exp_v" "$l" "nproc"
	fi
	# 4. memory, 5. disk: the subject asks for the available amount and the
	# usage %; "used/total" is the layout of its example only
	# amount + percentage: at least two numbers, one of them with a %
	amount_pct() {
		printf '%s' "$1" | grep -qE "$NUM ?%" &&
			[ "$(printf '%s' "$1" | grep -oE "$NUM" | wc -l)" -ge 2 ]
	}
	l=$(line_of 'mem|ram')
	if ! amount_pct "$l"; then
		ko "available RAM and usage %" "#Memory Usage: 74/987MB (7.50%)" "${l:-no 'Memory' line}" "free -m"
	elif printf '%s' "$l" | grep -qE "$NUM ?[KMGkmg]?i?[Bb]? ?/ ?$NUM"; then
		ok "memory used/total + %"
	else
		warn "memory line is not used/total like the subject example" "$l" "free -m"
	fi
	l=$(line_of 'disk|storage')
	if ! amount_pct "$l"; then
		ko "available storage and usage %" "#Disk Usage: 1009/2Gb (49%)" "${l:-no 'Disk' line}" "df -h --total"
	elif printf '%s' "$l" | grep -qE "$NUM ?[KMGTkmgt]?i?[Bb]? ?/ ?$NUM"; then
		ok "disk used/total + %"
	else
		warn "disk line is not used/total like the subject example" "$l" "df -h --total"
	fi
	# 6. CPU load
	l=$(line_of 'load|cpu usage|cpu use' 'mem|disk|ram|physical|vcpu')
	if printf '%s' "$l" | grep -qE "$NUM ?%"; then
		ok "CPU load %"
	else
		ko "CPU utilization %" "#CPU load: 6.7%" "${l:-no 'CPU load' line}" "top -bn1 | head -n3"
	fi
	# 7. last boot
	l=$(line_of 'boot')
	wb=$(who -b 2>/dev/null | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}')
	gb=$(printf '%s' "$l" | grep -oE '[0-9]{4}-[0-9]{2}-[0-9]{2} [0-9]{2}:[0-9]{2}')
	if [ -z "$l" ] || ! printf '%s' "$l" | grep -qE '[0-9]{1,2}:[0-9]{2}'; then
		ko "date and time of last reboot" "#Last boot: <date> <hh:mm> (who -b: $(who -b 2>/dev/null | sed 's/^ *system boot *//'))" "${l:-no 'Last boot' line}" "who -b"
	elif [ -n "$wb" ] && [ -n "$gb" ] && [ "$wb" != "$gb" ]; then
		warn "last boot shows $gb, who -b says $wb" "" "who -b"
	else
		ok "last boot: ${gb:-$l}"
	fi
	# 8. LVM
	l=$(line_of 'lvm')
	exp_l=no
	lsblk -rno TYPE 2>/dev/null | grep -qx lvm && exp_l=yes
	g=$(printf '%s' "$l" | grep -oiwE 'yes|no|active|inactive' | tail -n1 | tr '[:upper:]' '[:lower:]')
	case $g in active) g=yes ;; inactive) g=no ;; esac
	if [ "$g" = "$exp_l" ]; then
		ok "LVM use: $g"
	else
		ko "LVM active yes/no" "#LVM use: $exp_l" "${l:-no 'LVM' line}" "lsblk | grep lvm"
	fi
	# 9. TCP connections
	l=$(line_of 'tcp|connexion|connection')
	g=$(printf '%s' "$l" | grep -oE '[0-9]+' | head -n1)
	exp_t=$(ss -Htn state established 2>/dev/null | wc -l)
	if [ -z "$g" ]; then
		ko "number of active connections" "#TCP Connections: $exp_t ESTABLISHED" "${l:-no 'TCP' line}" "ss -tn state established"
	elif [ "$g" != "$exp_t" ]; then
		warn "TCP connections shows $g, ss says $exp_t now" "$l" "ss -Htn state established | wc -l"
	else
		ok "TCP connections: $g"
	fi
	# 10. users
	l=$(line_of 'user' 'sudo')
	g=$(printf '%s' "$l" | grep -oE '[0-9]+' | tail -n1)
	exp_u=$(who 2>/dev/null | wc -l)
	if [ -z "$g" ]; then
		ko "number of users using the server" "#User log: $exp_u" "${l:-no 'User log' line}" "who | wc -l"
	elif [ "$g" != "$exp_u" ]; then
		warn "user count shows $g, who says $exp_u" "$l" "who | wc -l"
	else
		ok "users logged in: $g"
	fi
	# 11. network
	l=$(line_of 'network|ip|mac')
	ip4=$(printf '%s' "$l" | grep -oE '([0-9]{1,3}\.){3}[0-9]{1,3}' | head -n1)
	mac=$(printf '%s' "$l" | grep -oiE '([0-9a-f]{2}:){5}[0-9a-f]{2}' | head -n1 | tr '[:upper:]' '[:lower:]')
	all_ip=" $(hostname -I 2>/dev/null) "
	all_mac=" $(cat /sys/class/net/*/address 2>/dev/null | oneline) "
	if [ -z "$ip4" ] || [ -z "$mac" ]; then
		ko "IPv4 address and MAC address" "#Network: IP 10.0.2.15 (08:00:27:51:9b:a5)" "${l:-no 'Network' line}" "hostname -I; ip link"
	else
		ok "network: IP $ip4, MAC $mac"
		case $all_ip in *" $ip4 "*) ;; *) warn "IP $ip4 is not one of the server addresses:$all_ip" "" "hostname -I" ;; esac
		case $all_mac in *" $mac "*) ;; *) warn "MAC $mac is not one of the interfaces" "$all_mac" "ip link" ;; esac
	fi
	# 12. sudo commands
	l=$(line_of 'sudo')
	g=$(printf '%s' "$l" | grep -oE '[0-9]+' | head -n1)
	if [ -n "$g" ]; then
		ok "sudo commands: $g"
	else
		ko "number of commands run with sudo" "#Sudo: 42 cmd" "${l:-no 'Sudo' line}" "journalctl _COMM=sudo | grep -c COMMAND"
	fi
fi

# --------------------------------------------------------------------------
section "summary"
# --------------------------------------------------------------------------

info "not checkable here: password strength of existing accounts, VM disk signature,"
info "snapshots (run run.py on the host), your understanding at the defense."
printf '\n'
if [ "$N_KO" -eq 0 ]; then
	printf '%sRESULT %d OK, 0 KO, %d WARN%s\n' "$C_B$C_G" "$N_OK" "$N_WARN" "$C_Z"
	exit 0
fi
printf '%sRESULT %d OK, %d KO, %d WARN%s\n' "$C_B$C_R" "$N_OK" "$N_KO" "$N_WARN" "$C_Z"
for k in "${KO_LIST[@]}"; do
	printf '  %sKO%s %s\n' "$C_R" "$C_Z" "$k"
done
exit 1
