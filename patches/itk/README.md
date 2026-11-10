# ITK performance patches

The superbuild applies these to the ITK commit pinned in `SuperBuild/External_ITKv6.cmake`
(`18d89fd`), in file-name order. They form a stack (each assumes the previous ones).
Turn them off with `-DANTS_ITK_PERFORMANCE_PATCHES=OFF` to get stock ANTs.

| Patch | What it does | Changes results? |
|---|---|---|
| `0001-step-timing.patch` | Timing scopes in the SyN loop plus an iteration counter on the field inverter. Silent unless `ANTS_STEP_TIMING=1` (per-level totals on stderr) or `ANTS_STEP_TRACE=1` (also begin/end markers). | No |
| `0002-cc-fast-path.patch` | CC (`ANTSNeighborhoodCorrelation`) metric: when fixed and moving images are on the virtual grid with identity transforms, which is what SyN sets up, samples are read straight from the buffers and summed a line at a time. Image masks are supported. Set `ITK_ANTS_CC_FAST_PATH=0` at run time to use the original code. | Bit-identical when voxel centres are exact in float. Otherwise it differs only where the original rounded sample positions to float (about 1e-4 relative per metric call). |
| `0003-syn-loop.patch` | Inside the SyN loop: resampled images and masks are reused within an iteration, the identity transform is cached per level, the metric derivative is written into the gradient field in place, the max-norm and boundary loops run in parallel, Gaussian smoothing is done with direct separable loops, and the zero-variance smoothing copy is gone. In the inverter and compose steps: parallel copies, fewer allocations, and one reused compose buffer. | No |

Measured on a 24-thread desktop, toy volumes 128³–256³, `SyN[0.2,3,0]`, 50x20x10:

| | CC | Mattes |
|---|---|---|
| SyN stage | 10–11x faster | 1.5x faster |
| Peak memory | -9 to -10% | -7% |

Profiling, benchmarks and verification scripts: the `ants-profiling` project.

## Updating the ITK pin

If `GIT_TAG` changes, the patches may stop applying, and the build then fails at the ITK
patch step with the name of the patch. Rebase the patches onto the new tag, or set
`-DANTS_ITK_PERFORMANCE_PATCHES=OFF`.
The patch step runs once, after ITK is downloaded. After editing a patch, delete
`<build>/ITKv6` (and `<build>/ITKv6-prefix`) so that ITK is downloaded and patched again.
