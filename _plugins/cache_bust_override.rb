require "digest/md5"

module Jekyll
  module CacheBustOverride
    def bust_css_cache(file_name)
      files = Dir["_sass/**/*", "assets/css/**/*"].reject { |path| File.directory?(path) }.sort
      digest = Digest::MD5.hexdigest(files.map { |path| File.read(path) }.join)

      "#{file_name}?v=#{digest}"
    end
  end
end

Liquid::Template.register_filter(Jekyll::CacheBustOverride)
