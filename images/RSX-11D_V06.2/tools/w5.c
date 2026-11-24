#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>

extern int atoi();

int main(argc, argv)
int argc;
char *argv[];
{
   int num = 0;

   if (argc == 2)
   {
      num = atoi(argv[1]);
   }

   if ( !(num>=0 && num<=99999) )
   {
      exit(1);
   }

   printf("%5d", num);
}
