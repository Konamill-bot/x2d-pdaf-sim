.PHONY: all asan check test clean
CC      ?= cc
CFLAGS  ?= -O2 -std=c99 -Wall -Wextra -Wpedantic -Werror -Wconversion -Wshadow
SRC     := af_dual_gated.c af_phase.c af_track.c af_chain.c
HDR     := af_util.h af_dual_gated.h af_phase.h af_track.h af_chain.h

all: libafc_f.so libafc_d.so bench af_demo test_c

libafc_f.so: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -fPIC -shared -o $@ $(SRC) -lm
libafc_d.so: $(SRC) $(HDR)
	$(CC) $(CFLAGS) -DAF_REAL_DOUBLE -fPIC -shared -o $@ $(SRC) -lm
bench: bench.c $(SRC) $(HDR)
	$(CC) $(CFLAGS) -o $@ bench.c $(SRC) -lm
af_demo: af_demo.c $(SRC) $(HDR)
	$(CC) $(CFLAGS) -o $@ af_demo.c $(SRC) -lm
test_c: test_c.c $(SRC) $(HDR)
	$(CC) $(CFLAGS) -o $@ test_c.c $(SRC) -lm

asan: bench.c test_c.c af_demo.c $(SRC) $(HDR)
	$(CC) -O1 -g -std=c99 -fsanitize=address,undefined -fno-omit-frame-pointer -o bench_asan bench.c $(SRC) -lm
	$(CC) -O1 -g -std=c99 -fsanitize=address,undefined -fno-omit-frame-pointer -o test_c_asan test_c.c $(SRC) -lm
	$(CC) -O1 -g -std=c99 -fsanitize=address,undefined -fno-omit-frame-pointer -o af_demo_asan af_demo.c $(SRC) -lm

check: test_c                   # pure C, no Python needed
	./test_c

test: libafc_f.so libafc_d.so   # C vs the Python reference models
	python3 test_equiv.py
	python3 closed_loop.py

clean:
	rm -f *.so bench test_c af_demo bench_asan test_c_asan af_demo_asan
