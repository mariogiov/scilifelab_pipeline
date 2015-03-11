"""QC workflow-specific code."""

import subprocess
import sys

from ngi_pipeline.log.loggers import minimal_logger
from ngi_pipeline.utils.classes import with_ngi_config
from ngi_pipeline.utils.filesystem import load_modules
from ngi_pipeline.utils.pyutils import flatten

LOG = minimal_logger(__name__)


@with_ngi_config
def return_cl_for_workflow(workflow_name, input_files, output_dir, config=None, config_file_path=None):
    """Return an executable-ready bash command line.

    :param str workflow_name: The name of the workflow to be run.
    :param list input_files: A flat list of input fastq files or a list of lists of paired files
    :param str output_dir: The directory to which to write output files

    :returns: A list of the bash command line(s) to be executed.
    :rtype: list
    :raises NotImplementedError: If the workflow requested has not been implemented.
    """
    workflow_fn_name = "workflow_{}".format(workflow_name)
    # Get the local function if it exists
    try:
        workflow_function = getattr(sys.modules[__name__], workflow_fn_name)
    except AttributeError as e:
        error_msg = 'Workflow "{}" has no associated function.'.format(workflow_fn_name)
        LOG.error(error_msg)
        raise NotImplementedError(error_msg)
    LOG.info('Building command line for workflow "{}"'.format(workflow_name))
    return workflow_function(input_files, output_dir, config)


def workflow_qc(input_files, output_dir, config):
    """Generic qc (includes multiple specific qc utilities)."""
    cl_list = []
    cl_list.append(workflow_fastqc(input_files, output_dir, config))
    cl_list.append(workflow_fastq_screen(input_files, output_dir, config))
    return cl_list


def workflow_fastqc(input_files, output_dir, config):
    """The constructor of the FastQC command line."""

    # Get the path to the fastqc command
    try:
        fastqc_path = config.get["fastqc"]["path"]
    except (KeyError, TypeError):
        LOG.warn('Path to fastqc not specified in config file; '
                 'checking if it is on PATH')
        # May need to load modules to find it on PATH
        modules_to_load = config.get("qc", {}).get("load_modules")
        if modules_to_load:
            load_modules(modules_to_load)
        try:
            # If we get no error, fastqc is on the PATH
            subprocess.check_call("fastqc --version", shell=True)
        except (OSError, subprocess.CalledProcessError) as e:
            raise ValueError('Path to FastQC could not be found and it is not '
                             'available on PATH; cannot proceed with FastQC '
                             'workflow (error "{}")'.format(e))
        else:
            fastqc_path = "fastqc"
    num_threads = config.get("fastqc", {}).get("threads") or 1
    fastq_files = flatten(input_files) # FastQC cares not for your "read pairs"
    cl = ('mkdir -p {output_dir} && {fastqc_path} -t {num_threads} '
          '-o {output_dir} {fastq_files}'.format(output_dir=output_dir,
                                                 fastqc_path=fastqc_path,
                                                 num_threads=num_threads,
                                                 fastq_files=" ".join(fastq_files)))
    return cl


def workflow_fastq_screen(input_files, output_dir, config):
    fastq_screen_path = config["fastq_screen"]["path"]
    bowtie2_path = config["fastq_screen"]["path_to_bowtie2"]
    fqs_config_path = config["fastq_screen"]["path_to_config"]
    try:
        fastq_screen_cl = "{fastq_screen_path}"
    except KeyError as e:
        ## FIXME check this e.args[0] thing I'm just faking it
        raise ValueError('Could not get required value "{}" from config file'.format(e.args[0]))
