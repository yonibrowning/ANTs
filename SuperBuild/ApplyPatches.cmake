# Apply a directory of git patches to an external project's source tree, in file-name order.
#
#   cmake -DGIT_EXECUTABLE=... -DSOURCE_DIR=... -DPATCH_DIR=... -P ApplyPatches.cmake
#
# Used as the PATCH_COMMAND of the ITK external project. A patch that is already applied
# (it reverse-applies cleanly) is skipped, so re-running the step is harmless.

foreach(var GIT_EXECUTABLE SOURCE_DIR PATCH_DIR)
  if(NOT ${var})
    message(FATAL_ERROR "ApplyPatches.cmake: ${var} is not set")
  endif()
endforeach()

file(GLOB patches "${PATCH_DIR}/*.patch")
list(SORT patches)

foreach(patch IN LISTS patches)
  get_filename_component(name "${patch}" NAME)
  execute_process(
    COMMAND "${GIT_EXECUTABLE}" apply --reverse --check "${patch}"
    WORKING_DIRECTORY "${SOURCE_DIR}"
    RESULT_VARIABLE already_applied
    OUTPUT_QUIET ERROR_QUIET)
  if(already_applied EQUAL 0)
    message(STATUS "ITK patch ${name}: already applied")
    continue()
  endif()
  execute_process(
    COMMAND "${GIT_EXECUTABLE}" apply --whitespace=nowarn "${patch}"
    WORKING_DIRECTORY "${SOURCE_DIR}"
    RESULT_VARIABLE failed)
  if(failed)
    message(FATAL_ERROR "ITK patch ${name} does not apply to ${SOURCE_DIR}")
  endif()
  message(STATUS "ITK patch ${name}: applied")
endforeach()
