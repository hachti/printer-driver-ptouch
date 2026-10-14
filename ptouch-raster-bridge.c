/* SPDX-License-Identifier: MIT */
/* Preserve CUPS raster, or wrap Ghostscript's monochrome PNG pages in it. */

#include <cups/raster.h>
#include <errno.h>
#include <limits.h>
#include <math.h>
#include <png.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>

struct png_page {
	png_structp png;
	png_infop info;
	unsigned char *row;
};

static ssize_t write_output(void *ctx, unsigned char *data, size_t size)
{
	size_t written = fwrite(data, 1, size, ctx);

	if (!written && ferror(ctx))
		return -1;
	return written;
}

static int copy_raster(const unsigned char *signature, size_t size)
{
	unsigned char buffer[16384];
	size_t count;

	if (fwrite(signature, 1, size, stdout) != size)
		return -1;
	while ((count = fread(buffer, 1, sizeof(buffer), stdin))) {
		if (fwrite(buffer, 1, count, stdout) != count)
			return -1;
	}
	if (ferror(stdin))
		return -1;
	return 0;
}

static int convert_page(cups_raster_t *raster)
{
	struct png_page *page;
	cups_page_header2_t header = { 0 };
	png_uint_32 width, height, ppm_x, ppm_y, y;
	int depth, color, interlace, unit;
	size_t row_size, i;
	unsigned dpi_x, dpi_y;
	volatile int result = -1;

	page = calloc(1, sizeof(*page));
	if (!page)
		return -1;
	page->png = png_create_read_struct(PNG_LIBPNG_VER_STRING, NULL,
					 NULL, NULL);
	if (!page->png)
		goto out;
	page->info = png_create_info_struct(page->png);
	if (!page->info)
		goto out;
	if (setjmp(png_jmpbuf(page->png)))
		goto out;
	png_init_io(page->png, stdin);
	png_set_sig_bytes(page->png, 8);
	png_read_info(page->png, page->info);
	png_get_IHDR(page->png, page->info, &width, &height, &depth,
		     &color, &interlace, NULL, NULL);
	if (!width || !height || width > UINT_MAX - 7 || depth != 1 ||
	    color != PNG_COLOR_TYPE_GRAY || interlace != PNG_INTERLACE_NONE) {
		fprintf(stderr, "ERROR: Expected non-interlaced 1-bit gray PNG\n");
		goto out;
	}
	if (!png_get_pHYs(page->png, page->info, &ppm_x, &ppm_y, &unit) ||
	    unit != PNG_RESOLUTION_METER || !ppm_x || !ppm_y) {
		fprintf(stderr, "ERROR: PNG has no physical resolution\n");
		goto out;
	}
	dpi_x = lround(ppm_x * 0.0254);
	dpi_y = lround(ppm_y * 0.0254);
	if (!dpi_x || !dpi_y) {
		fprintf(stderr, "ERROR: Invalid PNG resolution\n");
		goto out;
	}
	row_size = png_get_rowbytes(page->png, page->info);
	if (row_size != (width + 7) / 8)
		goto out;
	page->row = malloc(row_size);
	if (!page->row)
		goto out;

	header.HWResolution[0] = dpi_x;
	header.HWResolution[1] = dpi_y;
	header.cupsWidth = width;
	header.cupsHeight = height;
	header.cupsBitsPerColor = 1;
	header.cupsBitsPerPixel = 1;
	header.cupsBytesPerLine = row_size;
	header.cupsColorOrder = CUPS_ORDER_CHUNKED;
	header.cupsColorSpace = CUPS_CSPACE_K;
	header.cupsNumColors = 1;
	header.NumCopies = 1;
	header.cupsPageSize[0] = width * 72.0 / dpi_x;
	header.cupsPageSize[1] = height * 72.0 / dpi_y;
	header.PageSize[0] = lround(header.cupsPageSize[0]);
	header.PageSize[1] = lround(header.cupsPageSize[1]);
	header.cupsImagingBBox[2] = header.cupsPageSize[0];
	header.cupsImagingBBox[3] = header.cupsPageSize[1];
	header.ImagingBoundingBox[2] = header.PageSize[0];
	header.ImagingBoundingBox[3] = header.PageSize[1];
	if (!cupsRasterWriteHeader2(raster, &header))
		goto out;
	fprintf(stderr, "DEBUG: ptouch-raster-bridge: PNG %ux%u, %ux%u dpi\n",
		width, height, dpi_x, dpi_y);
	for (y = 0; y < height; y++) {
		png_read_row(page->png, page->row, NULL);
		/* PNG uses one for white; CUPS DeviceK uses one for black. */
		for (i = 0; i < row_size; i++)
			page->row[i] ^= 0xff;
		if (width % 8)
			page->row[row_size - 1] &= 0xff << (8 - width % 8);
		if (cupsRasterWritePixels(raster, page->row, row_size) != row_size)
			goto out;
	}
	png_read_end(page->png, page->info);
	result = 0;
out:
	free(page->row);
	if (page->png)
		png_destroy_read_struct(&page->png, &page->info, NULL);
	free(page);
	return result;
}

int main(int argc, char **argv)
{
	unsigned char signature[8];
	cups_raster_t *raster = NULL;
	size_t size;
	int result = 1;

	if (argc != 1) {
		fprintf(stderr, "Usage: %s < raster-or-png > cups-raster\n", argv[0]);
		return 1;
	}
	size = fread(signature, 1, sizeof(signature), stdin);
	if (!size && !ferror(stdin))
		return 0;
	if (size >= 4 && (!memcmp(signature, "RaSt", 4) ||
			  !memcmp(signature, "RaS2", 4) ||
			  !memcmp(signature, "RaS3", 4) ||
			  !memcmp(signature, "tSaR", 4) ||
			  !memcmp(signature, "2SaR", 4) ||
			  !memcmp(signature, "3SaR", 4))) {
		if (copy_raster(signature, size))
			goto out;
		result = 0;
		goto out;
	}
	if (size != sizeof(signature) || png_sig_cmp(signature, 0, size)) {
		fprintf(stderr, "ERROR: Expected CUPS raster or monochrome PNG\n");
		goto out;
	}
	raster = cupsRasterOpenIO(write_output, stdout, CUPS_RASTER_WRITE);
	if (!raster)
		goto out;
	for (;;) {
		if (convert_page(raster))
			goto out;
		size = fread(signature, 1, sizeof(signature), stdin);
		if (!size && !ferror(stdin))
			break;
		if (size != sizeof(signature) || png_sig_cmp(signature, 0, size)) {
			fprintf(stderr, "ERROR: Invalid following PNG page\n");
			goto out;
		}
	}
	result = 0;
out:
	if (raster)
		cupsRasterClose(raster);
	if (fflush(stdout))
		result = 1;
	if (result)
		fprintf(stderr, "ERROR: ptouch-raster-bridge conversion failed\n");
	return result;
}
