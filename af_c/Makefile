.PHONY: all asan test clean
CC      ?= cc
CFLAGS  ?= -O2 -std=c99 -Wall -Wextra -Wpedantic -Werror -Wconversion -Wshadow
all: libafdg_f.so libafdg_d.so bench
libafdg_f.so: af_dual_gated.c af_dual_gated.h
	$(CC) $(CFLAGS) -fPIC -shared -o $@ af_dual_gated.c -lm
libafdg_d.so: af_dual_gated.c af_dual_gated.h
	$(CC) $(CFLAGS) -DAF_REAL_DOUBLE -fPIC -shared -o $@ af_dual_gated.c -lm
bench: bench.c af_dual_gated.c af_dual_gated.h
	$(CC) $(CFLAGS) -o $@ bench.c af_dual_gated.c -lm
asan: af_dual_gated.c bench.c
	$(CC) -O1 -g -std=c99 -fsanitize=address,undefined -fno-omit-frame-pointer -o bench_asan bench.c af_dual_gated.c -lm
test: libafdg_f.so libafdg_d.so
	python3 test_equiv.py
	python3 closed_loop.py

clean:
	rm -f *.so bench bench_asan
