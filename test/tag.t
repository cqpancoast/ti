Setup

  $ export SHEET_FILE=$TMP/sheet-tag
  $ alias ti="$TESTDIR/../bin/ti"

When not working

  $ ti tag a-tag
  For all I know, you aren't working on anything. I don't know what to do.
  See `ti -h` to know how to start working.
  [1]

Not giving a tag to add

  $ ti on something
  Start working on \x1b[32msomething\x1b[39m. (esc)
  $ ti tag
  Please provide at least one tag to add.
