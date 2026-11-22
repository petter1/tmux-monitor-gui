#!/usr/bin/env bash
# Translations: update the POT/PO files and compile .mo files.
#
#   ./i18n.sh            update po/*.pot + po/*.po, compile to ./locale
#   ./i18n.sh compile    only compile (into ./locale or $1 of the next arg)
#   ./i18n.sh compile DIR
#
# Add a language:  msginit -i po/tmux-monitor.pot -l fr -o po/fr.po --no-translator
set -euo pipefail
cd "$(dirname "$0")"

DOMAIN=tmux-monitor
POT=po/$DOMAIN.pot

compile() {
    local out=${1:-locale}
    for po in po/*.po; do
        lang=$(basename "$po" .po)
        mkdir -p "$out/$lang/LC_MESSAGES"
        msgfmt --check -o "$out/$lang/LC_MESSAGES/$DOMAIN.mo" "$po"
    done
}

if [[ ${1:-} == compile ]]; then
    compile "${2:-}"
    exit 0
fi

xgettext --language=Python --from-code=UTF-8 \
    --keyword= --keyword=_:1c,2 \
    --package-name="$DOMAIN" --add-comments=TRANSLATORS \
    --sort-by-file -o "$POT" tmux_monitor.py

for po in po/*.po; do
    msgmerge --quiet --update --backup=none "$po" "$POT"
done
compile
echo "updated $POT, $(ls po/*.po | wc -l) PO file(s), compiled to ./locale"
