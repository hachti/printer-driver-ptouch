#                                               -*- Autoconf -*-
# Process this file with autoconf to produce a configure script.

AC_PREREQ([2.71])
AC_INIT([ptouch-driver],[1.7],[philpem@philpem.me.uk])
AM_INIT_AUTOMAKE([-Wall -Wno-portability])
AC_CONFIG_SRCDIR([rastertoptch.c])
AC_CONFIG_HEADERS([config.h])

# Checks for programs.
AC_PROG_CC
AC_PROG_INSTALL
AC_PATH_PROG([PYTHON3], [python3], [python3])

# Checks for libraries.
INITIAL_LIBS=$LIBS

AC_CHECK_LIB([m], [lrint])
LIBM=$LIBS
LIBS=$INITIAL_LIBS

AC_CHECK_LIB([cups], [cupsParseOptions])
LIBCUPS=$LIBS
LIBS=$INITIAL_LIBS

AC_CHECK_LIB([cupsimage], [cupsRasterReadHeader])
LIBCUPSIMAGE=$LIBS
LIBS=$INITIAL_LIBS

AC_CHECK_LIB([png], [png_write_row], [],
             [AC_MSG_ERROR([libpng development files are required])])
LIBPNG=$LIBS
LIBS=$INITIAL_LIBS

AC_SUBST([LIBM])
AC_SUBST([LIBCUPS])
AC_SUBST([LIBCUPSIMAGE])
AC_SUBST([LIBPNG])

# Checks for header files.
AC_CHECK_HEADERS([cups/cups.h cups/raster.h])
AC_CHECK_HEADERS([png.h], [], [AC_MSG_ERROR([png.h is required])])

# Checks for typedefs, structures, and compiler characteristics.
AC_C_CONST
AC_C_INLINE

# Checks for library functions.
AC_CHECK_FUNCS([strcasecmp strtol])

AC_CONFIG_FILES([Makefile
                 ptouch-driver.spec
                 ptouch-driver-foomatic.spec])
AC_OUTPUT
