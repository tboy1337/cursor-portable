cursor_portable_bin='__SEGMENT__'
case ":${PATH}:" in
    *":${cursor_portable_bin}:"*) ;;
    *) PATH="${cursor_portable_bin}:${PATH}"; export PATH ;;
esac
unset cursor_portable_bin
