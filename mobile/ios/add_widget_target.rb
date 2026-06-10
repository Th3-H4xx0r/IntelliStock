#!/usr/bin/env ruby
# Adds the PortfolioWidget WidgetKit app-extension target to Runner.xcodeproj,
# wires its Swift source / Info.plist / entitlements, gives Runner + the
# extension the App Group, and embeds the .appex into Runner.app.
# Idempotent: removes any existing PortfolioWidget target first.
$LOAD_PATH.unshift File.expand_path('~/.gem/ruby/2.6.0/gems/xcodeproj-1.27.0/lib')
require 'xcodeproj'

PROJECT   = File.join(__dir__, 'Runner.xcodeproj')
GROUP_ID  = 'group.dev.pkrishna.intellistock'
TEAM      = 'VY5CNF8734'
EXT_NAME  = 'PortfolioWidget'
EXT_BUNDLE = 'dev.pkrishna.intellistockMobile.PortfolioWidget'

project = Xcodeproj::Project.open(PROJECT)
runner  = project.targets.find { |t| t.name == 'Runner' }
raise 'Runner target not found' unless runner

# ── Idempotency: remove a previous PortfolioWidget target + embed phase ───────
project.targets.select { |t| t.name == EXT_NAME }.each do |t|
  runner.dependencies.delete_if { |d| d.target == t }
  t.remove_from_project
end
runner.copy_files_build_phases.select { |p| p.name == 'Embed Foundation Extensions' }.each(&:remove_from_project)

# ── Create the app-extension target ───────────────────────────────────────────
ext = project.new_target(:app_extension, EXT_NAME, :ios, '14.0', nil, :swift)

# Source group + files
group = project.main_group.find_subpath(EXT_NAME, true)
group.set_source_tree('SOURCE_ROOT')
swift_ref = group.files.find { |f| f.path&.end_with?('PortfolioWidget.swift') } ||
            group.new_file("#{EXT_NAME}/PortfolioWidget.swift")
ext.add_file_references([swift_ref])

# System frameworks the SwiftUI/WidgetKit code needs
ext.add_system_framework(%w[WidgetKit SwiftUI])

# Build settings for the extension
ext.build_configurations.each do |cfg|
  s = cfg.build_settings
  s['PRODUCT_BUNDLE_IDENTIFIER'] = EXT_BUNDLE
  s['PRODUCT_NAME'] = '$(TARGET_NAME)'
  s['INFOPLIST_FILE'] = "#{EXT_NAME}/Info.plist"
  s['CODE_SIGN_ENTITLEMENTS'] = "#{EXT_NAME}/#{EXT_NAME}.entitlements"
  s['CODE_SIGN_STYLE'] = 'Automatic'
  s['DEVELOPMENT_TEAM'] = TEAM
  s['IPHONEOS_DEPLOYMENT_TARGET'] = '14.0'
  s['SWIFT_VERSION'] = '5.0'
  s['TARGETED_DEVICE_FAMILY'] = '1,2'
  s['GENERATE_INFOPLIST_FILE'] = 'NO'
  s['SKIP_INSTALL'] = 'NO'
  s['CURRENT_PROJECT_VERSION'] = '1'
  s['MARKETING_VERSION'] = '1.0'
  s['SWIFT_EMIT_LOC_STRINGS'] = 'YES'
  s['LD_RUNPATH_SEARCH_PATHS'] = ['$(inherited)', '@executable_path/Frameworks', '@executable_path/../../Frameworks']
end

# ── App Group entitlement for Runner ──────────────────────────────────────────
runner_group = project.main_group.find_subpath('Runner', true)
unless runner_group.files.any? { |f| f.path&.end_with?('Runner.entitlements') }
  runner_group.new_file('Runner/Runner.entitlements')
end
runner.build_configurations.each do |cfg|
  cfg.build_settings['CODE_SIGN_ENTITLEMENTS'] = 'Runner/Runner.entitlements'
end

# ── Runner depends on + embeds the extension ──────────────────────────────────
runner.add_dependency(ext)
embed = runner.new_copy_files_build_phase('Embed Foundation Extensions')
embed.symbol_dst_subfolder_spec = :plug_ins
bf = embed.add_file_reference(ext.product_reference, true)
bf.settings = { 'ATTRIBUTES' => ['RemoveHeadersOnCopy'] }

# Avoid "Cycle inside Runner": the embed phase must run BEFORE Flutter's
# run-script ("Thin Binary") phases. Move it to right after Frameworks.
runner.build_phases.delete(embed)
fw_index = runner.build_phases.index do |p|
  p.is_a?(Xcodeproj::Project::Object::PBXFrameworksBuildPhase)
end
insert_at = fw_index ? fw_index + 1 : runner.build_phases.length
runner.build_phases.insert(insert_at, embed)

project.save
puts "OK: added #{EXT_NAME} target (#{EXT_BUNDLE}), App Group #{GROUP_ID}, embedded into Runner.app"
puts "Targets now: #{project.targets.map(&:name).join(', ')}"
