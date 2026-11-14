// How far into the image its code begins: the distance from the image's base
// to the code's first byte, both marked by the package's linker scripts, which
// Saw cannot name itself.

extern char _image_start[];
extern char _text_start[];

unsigned long sos_text_offset(void) {
    return (unsigned long)(_text_start - _image_start);
}
