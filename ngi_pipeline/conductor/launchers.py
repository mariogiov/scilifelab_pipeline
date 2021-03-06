#!/usr/bin/env python

from __future__ import print_function

import importlib

from ngi_pipeline.conductor.classes import NGIProject
from ngi_pipeline.database.classes import CharonSession, CharonError
from ngi_pipeline.log.loggers import minimal_logger
from ngi_pipeline.utils.classes import with_ngi_config
from ngi_pipeline.utils.communication import mail_analysis


LOG = minimal_logger(__name__)

@with_ngi_config
def launch_analysis(projects_to_analyze, restart_failed_jobs=False,
                    restart_finished_jobs=False, restart_running_jobs=False,
                    keep_existing_data=False, no_qc=False, exec_mode="sbatch",
                    quiet=False, manual=False, config=None, config_file_path=None):
    """Launch the appropriate analysis for each fastq file in the project.

    :param list projects_to_analyze: The list of projects (Project objects) to analyze
    :param dict config: The parsed NGI configuration file; optional/has default.
    :param str config_file_path: The path to the NGI configuration file; optional/has default.
    """
    for project in projects_to_analyze: # Get information from Charon regarding which best practice analyses to run
        try:
            engine = get_engine_for_bp(project, config, config_file_path)
        except (RuntimeError, CharonError) as e:
            LOG.error('Project {} could not be processed: {}'.format(project, e))
            continue
        engine.local_process_tracking.update_charon_with_local_jobs_status(config=config)
    charon_session = CharonSession()
    for project in projects_to_analyze:
        try:
            project_status = charon_session.project_get(project.project_id)['status']
        except CharonError as e:
            LOG.error('Project {} could not be processed: {}'.format(project, e))
            continue
        if not project_status == "OPEN":
            error_text = ('Data found on filesystem for project "{}" but Charon '
                          'reports its status is not OPEN ("{}"). Not launching '
                          'analysis for this project.'.format(project, project_status))
            LOG.error(error_text)
            if not config.get('quiet'):
                mail_analysis(project_name=project.name, level="ERROR", info_text=error_text)
            continue
        try:
            analysis_module = get_engine_for_bp(project)
        except (RuntimeError, CharonError) as e: # BPA missing from Charon?
            LOG.error('Skipping project "{}" because of error: {}'.format(project, e))
            continue
        if not no_qc:
            try:
                qc_analysis_module = load_engine_module("qc", config)
            except RuntimeError as e:
                LOG.error("Could not launch qc analysis: {}".format(e))
        for sample in project:
            # Launch QC analysis
            if not no_qc:
                try:
                    LOG.info('Attempting to launch sample QC analysis '
                             'for project "{}" / sample "{}" / engine '
                             '"{}"'.format(project, sample, qc_analysis_module.__name__))
                    qc_analysis_module.analyze(project=project,
                                               sample=sample,
                                               config=config)
                except Exception as e:
                    error_text = ('Cannot process project "{}" / sample "{}" / '
                                  'engine "{}" : {}'.format(project, sample,
                                                            analysis_module.__name__,
                                                            e))
                    LOG.error(error_text)
                    if not config.get("quiet"):
                        mail_analysis(project_name=project.name, sample_name=sample.name,
                                      engine_name=analysis_module.__name__,
                                      level="ERROR", info_text=e)
            # Launch actual best-practice analysis
            try:
                charon_reported_status = charon_session.sample_get(project.project_id,
                                                                   sample)['analysis_status']
                # Check Charon to ensure this hasn't already been processed
                if charon_reported_status == "UNDER_ANALYSIS":
                    if not restart_running_jobs:
                        error_text = ('Charon reports seqrun analysis for project "{}" '
                                      '/ sample "{}" does not need processing (already '
                                      '"{}")'.format(project, sample, charon_reported_status))
                        LOG.error(error_text)
                        if not config.get('quiet'):
                            mail_analysis(project_name=project.name, sample_name=sample.name,
                                          engine_name=analysis_module.__name__,
                                          level="ERROR", info_text=error_text)
                        continue
                elif charon_reported_status == "ANALYZED":
                    if not restart_finished_jobs:
                        error_text = ('Charon reports seqrun analysis for project "{}" '
                                      '/ sample "{}" does not need processing (already '
                                      '"{}")'.format(project, sample, charon_reported_status))
                        LOG.error(error_text)
                        if not config.get('quiet') and not config.get('manual'):
                            mail_analysis(project_name=project.name, sample_name=sample.name,
                                          engine_name=analysis_module.__name__,
                                          level="ERROR", info_text=error_text)
                        continue
                elif charon_reported_status == "FAILED":
                    if not restart_failed_jobs:
                        error_text = ('FAILED:  Project "{}" / sample "{}" Charon reports '
                                      'FAILURE, manual investigation needed!'.format(project, sample))
                        LOG.error(error_text)
                        if not config.get('quiet'):
                            mail_analysis(project_name=project.name, sample_name=sample.name,
                                          engine_name=analysis_module.__name__,
                                          level="ERROR", info_text=error_text)
                        continue
            except CharonError as e:
                LOG.error(e)
                continue
            try:
                LOG.info('Attempting to launch sample analysis for '
                         'project "{}" / sample "{}" / engine'
                         '"{}"'.format(project, sample, analysis_module.__name__))
                #actual analysis launch
                analysis_module.analyze(project=project,
                                        sample=sample,
                                        restart_finished_jobs=restart_finished_jobs,
                                        restart_running_jobs=restart_running_jobs,
                                        keep_existing_data=keep_existing_data,
                                        exec_mode=exec_mode,
                                        config=config)
            except Exception as e:
                error_text = ('Cannot process project "{}" / sample "{}" / '
                              'engine "{}" : {}'.format(project, sample,
                                                        analysis_module.__name__,
                                                        e))
                LOG.error(error_text)
                if not config.get("quiet"):
                    mail_analysis(project_name=project.name, sample_name=sample.name,
                                  engine_name=analysis_module.__name__,
                                  level="ERROR", info_text=e)
                continue


@with_ngi_config
def get_engine_for_bp(project, config=None, config_file_path=None):
    """returns a analysis engine module for the given project.

    :param NGIProject project: The project to get the engine from.
    """
    charon_session = CharonSession()
    try:
        best_practice_analysis = charon_session.project_get(project.project_id)["best_practice_analysis"]
        if not best_practice_analysis:
            raise KeyError("For once in my life ever can't you just fill in the forms properly")
    except KeyError:
        error_msg = ('No best practice analysis specified in Charon for '
                     'project "{}". Using "whole_genome_reseq"'.format(project))
        LOG.error(error_msg)
        best_practice_analysis = "whole_genome_reseq"
    try:
        analysis_module = load_engine_module(best_practice_analysis, config)
    except RuntimeError as e:
        raise RuntimeError('Project "{}": {}'.format(project, e))
    else:
        return analysis_module


def load_engine_module(best_practice_analysis, config):
    try:
        analysis_engine_module_name = config["analysis"]["best_practice_analysis"][best_practice_analysis]["analysis_engine"]
    except KeyError:
        error_msg = ('No analysis engine for best practice analysis "{}" '
                     'specified in configuration file.'.format(best_practice_analysis))
        raise RuntimeError(error_msg)
    try:
        analysis_module = importlib.import_module(analysis_engine_module_name)
    except ImportError as e:
        error_msg = ('best practice analysis "{}": couldn\'t import '
                     'module "{}": {}'.format(best_practice_analysis,
                                              analysis_engine_module_name, e))
        raise RuntimeError(error_msg)
    else:
        return analysis_module


