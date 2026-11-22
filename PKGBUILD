# Maintainer: petter1 <13876117+petter1@users.noreply.github.com>
pkgname=tmux-monitor-git
_pkgname=tmux-monitor
pkgver=r11.fbb146f
pkgrel=1
pkgdesc="Desktop window for tmux sessions and NVIDIA GPU usage, with a session archive"
arch=(any)
url="https://github.com/petter1/tmux-monitor-gui"
license=('MIT')
depends=(python python-pyqt6 tmux)
makedepends=(git gettext)
optdepends=('nvidia-utils: GPU panel')
provides=("$_pkgname")
conflicts=("$_pkgname")
source=("$_pkgname::git+$url.git")
sha256sums=('SKIP')

pkgver() {
    cd "$_pkgname"
    printf "r%s.%s" "$(git rev-list --count HEAD)" "$(git rev-parse --short=7 HEAD)"
}

build() {
    cd "$_pkgname"
    ./i18n.sh compile "$srcdir/locale"
}

package() {
    cd "$_pkgname"
    install -Dm755 tmux_monitor.py "$pkgdir/usr/bin/tmux-monitor"
    install -Dm755 tmux_monitor_archive.py "$pkgdir/usr/bin/tmux-monitor-archive"
    install -Dm644 tmux-monitor.desktop "$pkgdir/usr/share/applications/tmux-monitor.desktop"
    install -Dm644 README.md "$pkgdir/usr/share/doc/$_pkgname/README.md"
    install -Dm644 LICENSE "$pkgdir/usr/share/licenses/$pkgname/LICENSE"
    cp -r "$srcdir/locale" "$pkgdir/usr/share/locale"
}
