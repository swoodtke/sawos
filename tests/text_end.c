// The first address past the code segment, for cases that name it: the user
// linker scripts a package using this file links with define `_text_end` at
// the end of the code's output section, which Saw cannot name itself.

extern char _text_end[];

unsigned long sos_text_end(void) {
    return (unsigned long)_text_end;
}
