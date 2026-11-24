You've gunzipped and extracted rsx.tar.gz; now all
you have to do to create D62BOOT_patched.TPC (a repaired
tape from which you can boot SimH to perform an
RSX-11D V6.2 sysgen) is to run the bash shell script
./create_patched_D62BOOT
and sit back.

It is assumed that you have a more-or-less standard
Unix-y environment -- Linux (or BSD), or Cygwin
or Msys on Windows, with the usual complement of tools:
dd, sed, wc, etc.  And a C compiler, to build some
very small and uncomplicated helper programs.
(This may work in a terminal on Mac OS X as well, but
I don't know much about Macs.)

I've provided a SimH configuration file (sysgen.RP06L68K.cnf)
that can be used as a starting point for RSX-11D system
generation.  I haven't provided SimH itself -- download
simh-master.zip source code from GitHub and run "make pdp11"
(on Linux; you're better off getting a pre-built binary
for Windows) to create the PDP-11 emulator executable.

Have fun!

P.S.  The file "D62BOOT.index.txt" is a "master list" of
the 198 files on D62BOOT.TPC (and the patched version,
D62BOOT_patched.TPC).  The names on the left are what
FLX shows when listing the tape; the names on the right
are the names on disk after the sysgen program CPY puts
them there.  The (FLX-reported) block counts on the left
no longer reflect the blocking on D62BOOT_patched.TPC,
and should be ignored.
