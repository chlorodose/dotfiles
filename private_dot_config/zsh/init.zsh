ZPFX="${XDG_DATA_HOME:-${HOME}/.local/share}/zsh"
[ -d "$ZPFX" ] || mkdir -p "${ZPFX}"

declare -A ZINIT
ZINIT[HOME_DIR]="${ZPFX}"
ZINIT[BIN_DIR]="${ZINIT[HOME_DIR]}/plugins/zinit"

HISTSIZE=65536
HISTFILE="${ZPFX}/histfile"
[ -f "$HISTFILE" ] || touch "${HISTFILE}"
SAVEHIST=2147483648

setopt autocd beep hist_ignore_dups nonomatch extendedglob notify
bindkey -v

if [ ! -f "${ZINIT[BIN_DIR]}/zinit.zsh" ]; then
    echo "Zinit not exist, installing...."
    rm -rf "$(dirname ${ZINIT[BIN_DIR]})"
    mkdir -p "$(dirname ${ZINIT[BIN_DIR]})"
	# Maybe an flock logic?
    git clone https://github.com/zdharma-continuum/zinit.git "${ZINIT[BIN_DIR]}" --depth 1
fi

source "${ZINIT[BIN_DIR]}/zinit.zsh"

zi for \
	zsh-users/zsh-autosuggestions \
	zdharma-continuum/history-search-multi-word \
	zdharma-continuum/fast-syntax-highlighting
zi ice has'starship' atinit"sed -i 's/::STARSHIP::/starship/g' starship.zsh"; \
	zi snippet "https://raw.githubusercontent.com/starship/starship/refs/heads/main/src/init/starship.zsh"
zi for \
	OMZP::sudo

[ "$TERM" = "kitty" ] && zi load OMZP::kitty

zicompinit