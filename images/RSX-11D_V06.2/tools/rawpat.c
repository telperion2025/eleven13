#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>

#define BLOCKSIZE 4096

extern int atoi();

int main(argc, argv)
int argc;
char *argv[];
{
   FILE *fp_pat, *fp_raw;
   int fileno = 0;
   char patfile_name[10], rawfile_name[10];
   char new_word_str[10];
   u_int16_t new_word;
   int count = 0;
   long patch_loc;

   if (argc == 2)
   {
      fileno = atoi(argv[1]);
   }

   if ( !(fileno>=1 && fileno<=198) )
   {
      printf("usage: rawpat <number of file to extract>\n");
      printf("       file number must be >=1 and <=198\n");
      exit(1);
   }

   sprintf(patfile_name, "patch.%03d", fileno);
   if ( (fp_pat = fopen(patfile_name, "r")) == NULL )
   {
         printf("file %s not found\n", patfile_name);
         exit(1);
   }

   sprintf(rawfile_name, "R%03d.raw", fileno);
   if ( (fp_raw = fopen(rawfile_name, "r+b")) == NULL )
   {
         printf("file %s not found\n", rawfile_name);
         exit(1);
   }

   fgets(new_word_str, sizeof(new_word_str), fp_pat);
   while( !feof(fp_pat) )
   {
      count++;
      patch_loc = (BLOCKSIZE * count) - 2;
      if (new_word_str[3] == '?')
      {
          goto next_patch;
      }
      else
      {
         new_word = (u_int16_t)atoi(new_word_str);
      }

      fseek(fp_raw, patch_loc, SEEK_SET);
      fwrite(&new_word, sizeof(u_int16_t), 1, fp_raw);
fprintf(stderr, "%02d  :  %5d  :  %5u\n", count, patch_loc, new_word);
fflush(stderr);

next_patch:
      fgets(new_word_str, sizeof(new_word_str), fp_pat);
      new_word = (u_int16_t)atoi(new_word_str);
   }
}
