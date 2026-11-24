#include <unistd.h>
#include <stdio.h>
#include <stdlib.h>

#define MAX_RECSIZE 5000

extern int atoi();

int main()
{
   FILE *in_fp, *out_fp = NULL;
   int new_file_found = 0;
   int boot_file_found = 1;
   int large_file_found = 0;
   int next_record_is_header = 1;
   int pending_header = 0;
   int fileno;
   char in_path[] = "../original_TPC_tape_images/D62BOOT.TPC";
   char out_path[100], out_fname[20];
   char out_boot_file_prefix[] = "./files_extracted_from_D62BOOT_TPC/boot_files/";
   char out_small_file_prefix[] = "./files_extracted_from_D62BOOT_TPC/small_files/";
   char out_large_file_prefix[] = "./files_extracted_from_D62BOOT_TPC/large_files/";
   u_int8_t record[MAX_RECSIZE], header_record[40];
   u_int16_t reclen, actual_reclen;
   u_int16_t header_reclen = 18;
   int i;
   int quotient;

   in_fp = fopen(in_path, "rb");
   if (in_fp == NULL)
   {
fprintf(stderr, "Can't open '%s' for input\n", in_path);
fflush(stderr);
   }

   fileno = 1;
   sprintf(out_fname, "D%03d.TPC", fileno);

   while(fileno <= 198)
   {
next_record:
      *(u_int8_t *)&reclen = fgetc(in_fp);
      *( (u_int8_t *)&reclen + 1 ) = fgetc(in_fp);

      if (reclen == 4095)
      {
         if ( (ftell(in_fp)-2) == 2152418 )
         {
fprintf(stderr, "XXXXX\n");
fflush(stderr);

            reclen = 8;
         }
         else if ( (ftell(in_fp)-2) == 2161798 )
         {
fprintf(stderr, "XXXXX\n");
fflush(stderr);

            reclen = 36;
         }
         else
         {
fprintf(stderr, "__________ Discarding reclen '4095' _________________\n");
fflush(stderr);
            large_file_found = 1;

            *(u_int8_t *)&reclen = fgetc(in_fp);
            *( (u_int8_t *)&reclen + 1 ) = fgetc(in_fp);
         }
      }
fprintf(stderr, "fileno '%u'; pos '%u'; nominal reclen = '%u'\n", fileno, ftell(in_fp)-2, reclen);
fflush(stderr);

      if (feof(in_fp))
      {
         fprintf(stderr, "Bad input tape format\n");
         fflush(stderr);
         exit(-1);
      }
      else if (reclen == 0)
      {
fprintf(stderr, "+++++++++++++++++  END OF %s  +++++++++++++++++\n", out_fname);
fflush(stderr);
         fputc((u_int8_t)reclen, out_fp);
         fputc((u_int8_t)(reclen>>8), out_fp);
         fflush(out_fp);

         fileno++;
         sprintf(out_fname, "D%03d.TPC", fileno);
         new_file_found = 1;
         next_record_is_header = 1;
         if (fileno > 4)
         {
            boot_file_found = 0;
         }

         *(u_int8_t *)&reclen = fgetc(in_fp);
         *( (u_int8_t *)&reclen + 1 ) = fgetc(in_fp);

         while ( !feof(in_fp) && (reclen == 0) )
         {
            *(u_int8_t *)&reclen = fgetc(in_fp);
            *( (u_int8_t *)&reclen + 1 ) = fgetc(in_fp);
         }

         if ( feof(in_fp) )
         {
            if (fileno <= 198)
            {
               fprintf(stderr, "Bad input tape format\n");
               fflush(stderr);
            }
            exit(-1);
         }
         else
         {
fprintf(stderr, "fileno '%u'; pos '%u'; nominal reclen = '%u'\n", fileno, ftell(in_fp)-2, reclen);
fflush(stderr);
         }
      }

      if (new_file_found)
      {
         if (out_fp != NULL)
         {
            fclose(out_fp);
         }

         new_file_found = 0;
      }

      /* Force even actual reclen (round up to even) */

      quotient = reclen/2;
      if ( (quotient*2) < reclen )
      {
         actual_reclen = reclen + 1;
      }
      else
      {
         actual_reclen = reclen;
      }
fprintf(stderr, "actual reclen = '%d'\n", actual_reclen);
fflush(stderr);

      if (next_record_is_header)
      {
         for (i=0; i<actual_reclen; i++)
         {
            header_record[i] = fgetc(in_fp);
            if (feof(in_fp))
            {
               fprintf(stderr, "Bad input tape format\n");
               fflush(stderr);
               exit(-1);
            }
         }

         next_record_is_header = 0;
         pending_header = 1;
         goto next_record;
      }
      else
      {
         for (i=0; i<actual_reclen; i++)
         {
            record[i] = fgetc(in_fp);
            if (feof(in_fp))
            {
               fprintf(stderr, "Bad input tape format\n");
               fflush(stderr);
               exit(-1);
            }
         }
      }

      if (pending_header)
      {
         if (boot_file_found)
         {
            sprintf(out_path, "%s%s", out_boot_file_prefix, out_fname);
         }
         else if (large_file_found)
         {
            sprintf(out_path, "%s%s", out_large_file_prefix, out_fname);
            large_file_found = 0;
         }
         else
         {
            sprintf(out_path, "%s%s", out_small_file_prefix, out_fname);
         }
         out_fp = fopen(out_path, "wb");
         if (out_fp == NULL)
         {
fprintf(stderr, "Can't open '%s' for output\n", out_path);
fflush(stderr);
         }
         else
         {
fprintf(stderr, "Opened '%s' for output\n", out_path);
fflush(stderr);
         }

fprintf(stderr, "WRITING RECORD: header reclen = '%u'\n", header_reclen);
fflush(stderr);
         fputc((u_int8_t)header_reclen, out_fp);
         fputc((u_int8_t)(header_reclen>>8), out_fp);
         fflush(out_fp);
   
         for (i=0; i<header_reclen; i++)
         {
            fputc(header_record[i], out_fp);
            fflush(out_fp);
         }

         pending_header = 0;
      }

fprintf(stderr, "WRITING RECORD: nominal reclen = '%u'; actual reclen = '%u'\n",
                    reclen, actual_reclen);
fflush(stderr);
      fputc((u_int8_t)reclen, out_fp);
      fputc((u_int8_t)(reclen>>8), out_fp);
      fflush(out_fp);
   
      for (i=0; i<actual_reclen; i++)
      {
         fputc(record[i], out_fp);
         fflush(out_fp);
      }
   }
}
