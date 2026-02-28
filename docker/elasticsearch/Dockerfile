# Elasticsearch with IK Chinese Analyzer
# IK 版本必须与 ES 版本严格匹配
FROM elasticsearch:8.16.1

# 安装 IK 中文分词插件
RUN elasticsearch-plugin install --batch \
    https://get.infini.cloud/elasticsearch/analysis-ik/8.16.1

# 可选：安装 pinyin 拼音分词插件（支持拼音搜索）
# RUN elasticsearch-plugin install --batch \
#     https://get.infini.cloud/elasticsearch/analysis-pinyin/8.16.1
