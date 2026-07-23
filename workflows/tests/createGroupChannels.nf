include { createGroupChannels } from '../morphport.nf'

workflow TEST_CREATE_GROUP_CHANNELS {
    take:
    ch_partitioned
    ch_analysis_groups

    main:
    ch_result = createGroupChannels(ch_partitioned, ch_analysis_groups)

    emit:
    result = ch_result
}
