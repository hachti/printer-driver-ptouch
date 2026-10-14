/* SPDX-License-Identifier: GPL-2.0-or-later */
/* Read monochrome test rasters without depending on an image library. */

#include <cups/raster.h>
#include <stdio.h>
#include <stdlib.h>

int main(void)
{
	cups_raster_t *raster;
	cups_page_header2_t header;
	unsigned char *row;
	unsigned int x, y;
	int invert;

	raster = cupsRasterOpen(0, CUPS_RASTER_READ);
	if (!raster)
		return 1;
	while (cupsRasterReadHeader2(raster, &header)) {
		if (header.cupsBitsPerPixel != 1 ||
		    header.cupsBitsPerColor != 1 ||
		    (header.cupsColorSpace != CUPS_CSPACE_K &&
		     header.cupsColorSpace != CUPS_CSPACE_W))
			return 1;
		row = malloc(header.cupsBytesPerLine);
		if (!row)
			return 1;
		invert = header.cupsColorSpace == CUPS_CSPACE_W;
		printf("{\"width\":%u,\"height\":%u,\"stride\":%u,"
		       "\"xdpi\":%u,\"ydpi\":%u,\"data\":\"",
		       header.cupsWidth, header.cupsHeight,
		       header.cupsBytesPerLine,
		       header.HWResolution[0], header.HWResolution[1]);
		for (y = 0; y < header.cupsHeight; y++) {
			if (cupsRasterReadPixels(raster, row,
						header.cupsBytesPerLine) !=
			    header.cupsBytesPerLine)
				return 1;
			for (x = 0; x < header.cupsBytesPerLine; x++) {
				if (invert)
					row[x] ^= 0xff;
				printf("%02x", row[x]);
			}
		}
		puts("\"}");
		free(row);
	}
	cupsRasterClose(raster);
	return 0;
}
