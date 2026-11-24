#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>

extern int atoi();

int main(argc, argv)
int argc;
char *argv[];
{
   int fileno = 0;

   if (argc == 2)
   {
      fileno = atoi(argv[1]);
   }

   if ( !(fileno>=1 && fileno<=198) )
   {
      exit(1);
   }

   printf("%03d", fileno);
}
