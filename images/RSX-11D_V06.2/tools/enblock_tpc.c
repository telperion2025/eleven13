#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>
#include <errno.h>

void putint(int);

int main(argc, argv)
int argc;
char *argv[];
{
	int c;
	int bsize;
	char *buf;

	switch (argc) {
	case 1:
		bsize = 512;
		break;
	case 2:
		bsize = atoi(argv[1]);
	        break;
	default:
		bsize = 0;
	}
	if (bsize == 0) {
		printf("usage: enblock_tpc [blocksize]\n");
		exit(1);
	}
	if((buf = malloc(bsize)) == 0) {
		printf("error: malloc: %d", errno);
		exit(1);
	}
	while ((c = read(0, buf, bsize)) >0) {
		if (c == bsize)
		{
			putint(bsize);
			write(1, buf, bsize);
		}
		else
		{
			putint(c);
			write(1, buf, c);
		}
	}
}

void putint(n)
int n;
{
	char obuf[2];

	obuf[0] = n % 256;
	n /= 256;
	obuf[1] = n % 256;
	write(1, obuf, 2);
}
