# Maintainer: <name> <email>
pkgname=tmux-monitor-git
_pkgname=tmux-monitor
pkgver=r5.5260ea0
pkgrel=1
pkgdesc="Desktop window for tmux sessions and NVIDIA GPU usage, with a session archive"
arch=(any)
url="https://github.com/<user>/<repo>"
license=('unknown')  # TODO: set once the license is chosen
depends=(python python-pyqt6 tmux)
makedepends=(git gettext)
optdepends=('nvidia-utils: GPU panel')
provides=("$_pkgname")
conflicts=("$_pkgname")
# Builds the committed state of this local checkout.
# For the AUR: source=("$_pkgname::git+$url.git")
source=("$_pkgname::git+file://$startdir")
sha256sums=('SKIP')

pkgver() {
    cd "$_pkgname"
    printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short=7 HEAD)"
}

build() {
    cd "$_pkgname"
    ./i18n.sh compile "$srcdir/locale"
    python tmux_monitor_archive.py hook-conf --exe /usr/bin/tmux-monitor-archive > "$srcdir/archive.tmux"
}

package() {
    cd "$_pkgname"
    install -Dm755 tmux_monitor.py "$pkgdir/usr/bin/tmux-monitor"
    install -Dm755 tmux_monitor_archive.py "$pkgdir/usr/bin/tmux-monitor-archive"
    install -Dm644 tmux-monitor.desktop "$pkgdir/usr/share/applications/tmux-monitor.desktop"
    install -Dm644 "$srcdir/archive.tmux" "$pkgdir/usr/share/tmux-monitor/archive.tmux"
    install -Dm644 README.md "$pkgdir/usr/share/doc/$_pkgname/README.md"
    cp -r "$srcdir/locale" "$pkgdir/usr/share/locale"
}
